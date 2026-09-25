"""AURIX engine.

agent_loop  : one agent working a request: model call -> permission check -> tools -> repeat
team_run    : Phase 4 pipeline: manager plans -> specialists do each step -> reviewer checks
              -> fix round if needed -> manager writes the final answer
run_turn    : a chat message (single agent or team), streamed to the UI
Background tasks (tasks.py) reuse agent_loop and team_run with their own context."""
import asyncio
import json
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable

from . import llm, memory, rag, settings
from .agents import AGENTS, TEAM_LABEL, route
from .config import WORKSPACE
from .permissions import PERMISSIONS, level_of
from .tools import TOOLS

SYSTEM = platform.system()


class Cancelled(Exception):
    pass


@dataclass
class Ctx:
    scope: str                                   # conversation id or task id
    origin: str                                  # "chat" or "task"
    title: str
    web: bool
    cfg: dict
    autonomy: str = "ask"
    persist: Callable[[dict, dict | None], None] | None = None   # store tool traffic (chats)
    cancelled: Callable[[], bool] = lambda: False
    permission_timeout: float = 600
    totals: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0,
                                                  "gen_ms": 0.0, "steps": 0})


# ---------- helpers ----------
def params_for(cfg: dict) -> dict:
    p = {"temperature": cfg["temperature"], "top_p": cfg["top_p"],
         "chat_template_kwargs": {"enable_thinking": bool(cfg["thinking"])}}
    if cfg["max_tokens"]:
        p["max_tokens"] = cfg["max_tokens"]
    return p


def tools_for(agent, web: bool):
    return [TOOLS[t] for t in agent.tools if t in TOOLS and (web or not TOOLS[t].needs_web)
            and (TOOLS[t].platform in (None, SYSTEM))]


def memory_block(cfg: dict, query: str) -> str:
    if not cfg.get("memory_in_prompt"):
        return ""
    facts = rag.relevant_facts(query)
    docs = memory.list_documents()
    out = ""
    if facts:
        out += "\n\nWhat you know about the user (long-term memory):\n" + "\n".join(f"- {f['text']}" for f in facts)
    if docs:
        out += (f"\n\nThe user's knowledge base has {len(docs)} document(s) "
                f"({', '.join(d['name'] for d in docs[:6])}{'…' if len(docs) > 6 else ''}). "
                "Use search_documents when a question may concern them.")
    return out


def system_prompt(agent, cfg: dict, query: str) -> str:
    text = (
        f"You are AURIX, a local AI assistant running from a USB drive on the user's {SYSTEM} "
        f"computer. Today is {datetime.now():%A %d %B %Y}.\n"
        f"Current role: {agent.label}. {agent.role}\n"
        f"The workspace folder for all file work is: {WORKSPACE}\n"
        "Rules: use a tool whenever it gives a more reliable answer than guessing (maths, files, "
        "current information, running code). Never invent tool results. If the user denies a tool, "
        "do not retry it; explain what you would have done instead. When the user shares a lasting "
        "preference or fact about themselves or their projects, save it with remember. Answer "
        "concisely, and use Markdown with language-tagged code blocks."
    )
    if cfg["custom_instructions"].strip():
        text += "\n\nUser instructions:\n" + cfg["custom_instructions"].strip()
    return text + memory_block(cfg, query)


def _args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


def _call(tool, args: dict) -> str:
    missing = [k for k in tool.parameters.get("required", []) if k not in args]
    if missing:
        return f"Error: missing argument(s): {', '.join(missing)}"
    known = tool.parameters.get("properties", {})
    try:
        return str(tool.func(**{k: v for k, v in args.items() if k in known}))
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


async def choose_model(cfg: dict, tier: str, forced: str = "") -> tuple[str | None, str]:
    try:
        available = await llm.list_models()
    except Exception:  # noqa: BLE001
        available = []
    forced = forced or cfg["model_override"]
    if forced and forced in available:
        return forced, "model chosen by you"
    return llm.pick_model(available, tier, cfg["models"]), f"{tier} tier"


# ---------- one agent ----------
async def agent_loop(ctx: Ctx, agent, model: str, messages: list[dict]) -> AsyncIterator[dict]:
    """Yields UI events; the last event is {"type": "_final", "content": ...}."""
    cfg = ctx.cfg
    tools = tools_for(agent, ctx.web)
    names = {t.name for t in tools}
    schemas = [t.schema() for t in tools]
    params = params_for(cfg)
    max_steps = cfg["max_tool_steps"]

    for step in range(max_steps + 1):
        if ctx.cancelled():
            raise Cancelled()
        status = await llm.model_status(model)
        yield {"type": "status", "state": "loading" if status != "loaded" else "generating", "model": model}
        final = None
        async for ev in llm.stream_chat(model, messages, schemas if step < max_steps else [], params):
            if ev["type"] == "end":
                final = ev
            else:
                yield ev
        t = final.get("timings") or {}
        ctx.totals["prompt_tokens"] += int(t.get("prompt_n", 0))
        ctx.totals["completion_tokens"] += int(t.get("predicted_n", 0))
        ctx.totals["gen_ms"] += float(t.get("predicted_ms", 0))
        ctx.totals["steps"] += 1

        if not final["tool_calls"]:
            yield {"type": "_final", "content": final["content"]}
            return

        assistant_msg = {"role": "assistant", "content": final["content"], "tool_calls": final["tool_calls"]}
        messages.append(assistant_msg)
        if ctx.persist:
            ctx.persist(assistant_msg, {"agent": agent.name, "model": model})

        for tc in final["tool_calls"]:
            if ctx.cancelled():
                raise Cancelled()
            name = tc["function"]["name"]
            args = _args(tc["function"]["arguments"])
            level = level_of(name)
            yield {"type": "tool_call", "id": tc["id"], "name": name, "args": args, "level": level,
                   "agent": agent.name}
            if name not in names:
                result, ok = f"Error: tool '{name}' is not available to the {agent.label} agent.", False
            else:
                allowed = PERMISSIONS.auto_allowed(ctx.scope, name, ctx.autonomy)
                if not allowed:
                    pid, fut = PERMISSIONS.request(ctx.scope, name, args, level, ctx.origin, ctx.title)
                    yield {"type": "permission", "id": pid, "call_id": tc["id"], "name": name, "args": args,
                           "level": level, "conversation_id": ctx.scope, "origin": ctx.origin}
                    try:
                        allowed = await asyncio.wait_for(fut, ctx.permission_timeout)
                    except asyncio.TimeoutError:
                        PERMISSIONS.cancel(pid)
                        allowed = False
                    yield {"type": "permission_result", "id": pid, "call_id": tc["id"], "allowed": allowed}
                if allowed:
                    t0 = time.monotonic()
                    result = await asyncio.to_thread(_call, TOOLS[name], args)
                    ok = not result.startswith("Error")
                    yield {"type": "tool_time", "id": tc["id"], "ms": int((time.monotonic() - t0) * 1000)}
                else:
                    result, ok = "The user denied permission for this action.", False
            result = result[:6000]
            yield {"type": "tool_result", "id": tc["id"], "name": name, "ok": ok, "output": result}
            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": name, "content": result}
            messages.append(tool_msg)
            if ctx.persist:
                ctx.persist(tool_msg, None)

    yield {"type": "_final", "content": f"(Stopped after {max_steps} tool steps.)"}


async def run_agent(ctx: Ctx, agent, model: str, messages: list[dict], sink: list) -> AsyncIterator[dict]:
    """agent_loop that stores its final text in sink[0] instead of yielding it."""
    async for ev in agent_loop(ctx, agent, model, messages):
        if ev["type"] == "_final":
            sink.append(ev["content"])
        else:
            yield ev


# ---------- Phase 4: team of agents ----------
SPECIALISTS = ["researcher", "coder", "analyst", "operator", "general"]
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object",
            "properties": {"agent": {"type": "string", "enum": SPECIALISTS},
                           "instruction": {"type": "string"},
                           "done_when": {"type": "string"}},
            "required": ["agent", "instruction", "done_when"]}},
    },
    "required": ["steps"],
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"pass": {"type": "boolean"},
                   "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                   "fix_agent": {"type": "string", "enum": SPECIALISTS},
                   "fix_instruction": {"type": "string"}},
    "required": ["pass", "issues", "fix_agent", "fix_instruction"],
}
AGENT_GUIDE = ("researcher: web research and reading pages; coder: writing, running and fixing code "
               "and files; analyst: data files and calculations with Python; operator: opening apps, "
               "controlling the browser and running n8n workflows; general: explanations and writing.")


def _results_text(steps: list[dict]) -> str:
    done = [s for s in steps if s.get("result") is not None]
    return "\n\n".join(f"Step {i + 1} ({s['agent']}): {s['instruction']}\nResult: {s['result'][:1500]}"
                       for i, s in enumerate(done)) or "(none yet)"


async def team_run(ctx: Ctx, goal: str, history: list[dict], plan: dict | None,
                   checkpoint: Callable[[dict], None], sink: list) -> AsyncIterator[dict]:
    cfg = ctx.cfg
    manager_model, _ = await choose_model(cfg, "strong")
    if not manager_model:
        raise RuntimeError("The model server is not reachable.")
    params = params_for(cfg)
    context = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-6:]) if history else ""

    # 1) plan (or resume a checkpointed plan)
    if not plan or not plan.get("steps"):
        yield {"type": "status", "state": "planning", "model": manager_model}
        raw = await llm.complete_json(manager_model, [
            {"role": "system", "content": "You are the manager of a small team of AI agents. Break the "
             "user's goal into 1 to 5 concrete steps, each done by one agent. " + AGENT_GUIDE +
             " Keep steps small and checkable. Do not add steps the goal does not need."},
            {"role": "user", "content": (f"Recent conversation:\n{context}\n\n" if context else "") + f"Goal: {goal}"},
        ], PLAN_SCHEMA, params)
        steps = [s for s in raw.get("steps", []) if s.get("agent") in SPECIALISTS and s.get("instruction")][:5]
        if not steps:
            steps = [{"agent": "general", "instruction": goal, "done_when": "the goal is answered"}]
        plan = {"steps": steps, "reviews": []}
        checkpoint(plan)
    yield {"type": "plan", "steps": [{k: s[k] for k in ("agent", "instruction", "done_when")} for s in plan["steps"]]}

    # 2) run each step with its specialist
    async def run_step(i: int, step: dict, extra: str = ""):
        agent = AGENTS[step["agent"]]
        model, _ = await choose_model(cfg, agent.model_tier)
        yield {"type": "step", "index": i, "state": "running", "agent": step["agent"],
               "label": agent.label, "instruction": step["instruction"], "model": model}
        msgs = [{"role": "system", "content": system_prompt(agent, cfg, step["instruction"])},
                {"role": "user", "content": f"Overall goal: {goal}\n\nYour step: {step['instruction']}\n"
                                            f"Done when: {step.get('done_when', '')}\n\nEarlier results:\n"
                                            f"{_results_text(plan['steps'])}{extra}"}]
        out: list = []
        for attempt in range(2):                              # retry a step once if it produced nothing
            async for ev in run_agent(ctx, agent, model, msgs, out):
                yield ev
            if out and out[-1].strip():
                break
            yield {"type": "step", "index": i, "state": "retrying", "agent": step["agent"]}
        step["result"] = (out[-1] if out else "") or "(no result)"
        checkpoint(plan)
        yield {"type": "step", "index": i, "state": "done", "agent": step["agent"], "result": step["result"][:600]}

    for i, step in enumerate(plan["steps"]):
        if step.get("result") is not None:
            yield {"type": "step", "index": i, "state": "done", "agent": step["agent"],
                   "result": step["result"][:600], "resumed": True}
            continue
        if ctx.cancelled():
            raise Cancelled()
        async for ev in run_step(i, step):
            yield ev

    # 3) review, with fix rounds
    for rnd in range(cfg["review_rounds"]):
        if ctx.cancelled():
            raise Cancelled()
        yield {"type": "status", "state": "reviewing", "model": manager_model}
        review = await llm.complete_json(manager_model, [
            {"role": "system", "content": "You are a strict reviewer. Check whether the step results "
             "fully achieve the goal. Pass only if nothing important is missing or wrong. If it fails, "
             "name the single most useful fix and which agent should do it. " + AGENT_GUIDE},
            {"role": "user", "content": f"Goal: {goal}\n\nResults:\n{_results_text(plan['steps'])}"},
        ], REVIEW_SCHEMA, params)
        passed = bool(review.get("pass", True))
        plan.setdefault("reviews", []).append({"pass": passed, "issues": review.get("issues", [])})
        checkpoint(plan)
        yield {"type": "review", "round": rnd + 1, "pass": passed, "issues": review.get("issues", []),
               "fix": None if passed else {"agent": review.get("fix_agent"), "instruction": review.get("fix_instruction")}}
        if passed:
            break
        fix = {"agent": review.get("fix_agent") if review.get("fix_agent") in SPECIALISTS else "general",
               "instruction": review.get("fix_instruction") or "Fix the issues found in review.",
               "done_when": "the reviewer's issues are resolved", "fix": True}
        plan["steps"].append(fix)
        checkpoint(plan)
        async for ev in run_step(len(plan["steps"]) - 1, fix,
                                 "\n\nReviewer issues:\n- " + "\n- ".join(review.get("issues", []))):
            yield ev

    # 4) final answer, streamed
    yield {"type": "status", "state": "generating", "model": manager_model}
    final_msgs = [{"role": "system", "content": "You are AURIX's manager. Write the final answer to the "
                   "user from your team's step results: what was done, the key output, and any files "
                   "created. Be concise; use Markdown."},
                  {"role": "user", "content": f"Goal: {goal}\n\nStep results:\n{_results_text(plan['steps'])}"}]
    content = []
    async for ev in llm.stream_chat(manager_model, final_msgs, [], params):
        if ev["type"] == "token":
            content.append(ev["text"])
            yield ev
        elif ev["type"] == "end":
            t = ev.get("timings") or {}
            ctx.totals["completion_tokens"] += int(t.get("predicted_n", 0))
            ctx.totals["gen_ms"] += float(t.get("predicted_ms", 0))
            ctx.totals["steps"] += 1
    sink.append("".join(content))


# ---------- chat turn ----------
def _stats(ctx: Ctx, started: float) -> dict:
    t = ctx.totals
    return {**t, "seconds": round(time.monotonic() - started, 1),
            "tokens_per_second": round(t["completion_tokens"] / (t["gen_ms"] / 1000), 1) if t["gen_ms"] else None}


async def run_turn(cid: str | None, message: str, agent_choice: str, web: bool,
                   model_choice: str = "", images: list[str] | None = None) -> AsyncIterator[dict]:
    cfg = settings.load()
    if not cid or not memory.conversation_exists(cid):
        cid = memory.new_conversation(message.strip().splitlines()[0] if message.strip() else "Chat")
    started = time.monotonic()
    ctx = Ctx(scope=cid, origin="chat", title=message[:60], web=web, cfg=cfg,
              persist=lambda m, meta: memory.add_message(cid, m, meta))
    history = memory.history_for_model(cid, cfg["history_messages"])
    user_content = message if not images else (
        [{"type": "text", "text": message}] + [{"type": "image_url", "image_url": {"url": u}} for u in images[:4]])

    try:
        if agent_choice == "team":
            model, _ = await choose_model(cfg, "strong", model_choice)
            if not model:
                raise RuntimeError("The model server is not reachable. Start it with the launcher, then try again.")
            memory.log_route(cid, message, "team", model, "chosen by you", True)
            yield {"type": "meta", "conversation_id": cid, "agent": "team", "agent_label": TEAM_LABEL,
                   "model": model, "reason": "chosen by you", "model_reason": "manager uses the strong tier"}
            memory.add_message(cid, {"role": "user", "content": message})
            sink: list = []
            plan_holder: dict = {}
            async for ev in team_run(ctx, message, history, None, lambda p: plan_holder.update(p), sink):
                yield ev
            stats = _stats(ctx, started)
            memory.add_message(cid, {"role": "assistant", "content": sink[-1] if sink else ""},
                               meta={"agent": "team", "model": model, "stats": stats,
                                     "plan": [s["instruction"] for s in plan_holder.get("steps", [])]})
            yield {"type": "stats", **stats}
            yield {"type": "done", "conversation_id": cid}
            return

        manual = agent_choice in AGENTS
        agent_name, reason = (agent_choice, "chosen by you") if manual else route(message, web)
        agent = AGENTS[agent_name]
        tier = "vision" if images else agent.model_tier
        model, model_reason = await choose_model(cfg, tier, model_choice)
        if not model:
            raise RuntimeError("The model server is not reachable. Start it with the launcher, then try again.")
        memory.log_route(cid, message, agent_name, model, reason, manual)
        yield {"type": "meta", "conversation_id": cid, "agent": agent_name, "agent_label": agent.label,
               "model": model, "reason": reason, "model_reason": model_reason}
        memory.add_message(cid, {"role": "user", "content": user_content})
        messages = [{"role": "system", "content": system_prompt(agent, cfg, message)}, *history,
                    {"role": "user", "content": user_content}]
        sink = []
        async for ev in run_agent(ctx, agent, model, messages, sink):
            yield ev
        stats = _stats(ctx, started)
        memory.add_message(cid, {"role": "assistant", "content": sink[-1] if sink else ""},
                           meta={"agent": agent_name, "model": model, "stats": stats})
        yield {"type": "stats", **stats}
        yield {"type": "done", "conversation_id": cid}
    except Cancelled:
        yield {"type": "error", "text": "Stopped."}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if images and ("image" in msg.lower() or "mmproj" in msg.lower() or "multimodal" in msg.lower()):
            msg += " (This model can't read images. See README: put the model in its own folder with its mmproj file.)"
        yield {"type": "error", "text": f"Model error: {msg}", "conversation_id": cid}
