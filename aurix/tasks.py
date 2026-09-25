"""Phase 10: long-running background tasks.

A task is a goal, not a single message. The task manager runs it in the background
(no browser tab needed), with:
- a queue (max concurrent tasks from Settings, default 1 to keep the Mac cool)
- schedules: once at a time, every N minutes, or daily at HH:MM
- checkpoints: team plans and finished step results are saved after every step,
  so a failed or interrupted task resumes where it stopped instead of starting over
- retries with backoff (3 attempts), then "failed"
- autonomy per task: "ask" (sensitive and dangerous actions wait for approval) or
  "trusted" (sensitive runs; dangerous still waits). Waiting approvals appear in the UI.
"""
import asyncio
import time
from datetime import datetime, timedelta

from . import memory, settings
from .agents import AGENTS
from .engine import Cancelled, Ctx, choose_model, run_agent, system_prompt, team_run
from .permissions import PERMISSIONS

MAX_ATTEMPTS = 3
SKIP_EVENTS = {"token", "reasoning", "tool_time"}


def next_run_for(schedule: dict | None, now: float | None = None) -> float | None:
    if not schedule:
        return None
    now = now or time.time()
    kind = schedule.get("type")
    if kind == "once":
        return float(schedule["at"])
    if kind == "interval":
        return now + max(1, int(schedule.get("minutes", 60))) * 60
    if kind == "daily":
        hh, mm = (int(x) for x in schedule.get("time", "09:00").split(":"))
        base = datetime.fromtimestamp(now)
        run = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if run.timestamp() <= now:
            run += timedelta(days=1)
        return run.timestamp()
    return None


def describe(schedule: dict | None) -> str:
    if not schedule:
        return "runs once, now"
    k = schedule.get("type")
    if k == "once":
        return f"once at {datetime.fromtimestamp(float(schedule['at'])):%d %b %H:%M}"
    if k == "interval":
        return f"every {schedule.get('minutes')} min"
    if k == "daily":
        return f"daily at {schedule.get('time')}"
    return "custom"


class TaskManager:
    def __init__(self) -> None:
        self.running: dict[str, asyncio.Task] = {}
        self.cancel_flags: set[str] = set()
        self._loop_task: asyncio.Task | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        for t in memory.list_tasks():                       # tasks interrupted by a restart
            if t["status"] in ("running", "waiting"):
                memory.update_task(t["id"], status="paused", error="Interrupted when AURIX stopped. Resume to continue from the last checkpoint.")
                memory.add_task_event(t["id"], {"type": "status", "state": "paused"})
        self._loop_task = asyncio.create_task(self._scheduler())

    async def _scheduler(self) -> None:
        while True:
            try:
                limit = settings.load()["max_concurrent_tasks"]
                now = time.time()
                for t in sorted(memory.list_tasks(), key=lambda x: x["created_at"]):
                    if len(self.running) >= limit:
                        break
                    due = t["status"] == "queued" or (t["status"] == "scheduled" and (t["next_run"] or 0) <= now)
                    if due and t["id"] not in self.running:
                        self.running[t["id"]] = asyncio.create_task(self._run(t["id"]))
            except Exception as e:  # noqa: BLE001
                print("task scheduler error:", e)
            await asyncio.sleep(2)

    # ---------- API ----------
    def create(self, goal: str, title: str | None, agent: str, autonomy: str, web: bool,
               schedule: dict | None) -> str:
        nr = next_run_for(schedule)
        status = "scheduled" if schedule and schedule.get("type") != "once" or (nr and nr > time.time() + 5) else "queued"
        tid = memory.create_task(title=(title or goal.split("\n")[0])[:80], goal=goal, agent=agent,
                                 autonomy=autonomy, status=status, schedule=schedule, next_run=nr, web=web)
        memory.add_task_event(tid, {"type": "created", "schedule": describe(schedule)})
        return tid

    def cancel(self, tid: str) -> None:
        self.cancel_flags.add(tid)
        PERMISSIONS.cancel_scope(tid)
        if tid not in self.running:
            memory.update_task(tid, status="cancelled")
            memory.add_task_event(tid, {"type": "status", "state": "cancelled"})

    def resume(self, tid: str) -> None:
        self.cancel_flags.discard(tid)
        memory.update_task(tid, status="queued", error=None, attempts=0)
        memory.add_task_event(tid, {"type": "status", "state": "queued", "note": "resumed from checkpoint"})

    def run_now(self, tid: str) -> None:
        self.cancel_flags.discard(tid)
        memory.update_task(tid, status="queued")

    def delete(self, tid: str) -> None:
        self.cancel(tid)
        memory.delete_task(tid)

    # ---------- execution ----------
    async def _run(self, tid: str) -> None:
        task = memory.get_task(tid)
        try:
            if not task:
                return
            self.cancel_flags.discard(tid)
            cfg = settings.load()
            memory.update_task(tid, status="running", error=None)
            log = lambda ev: memory.add_task_event(tid, ev)  # noqa: E731
            log({"type": "status", "state": "running", "attempt": task["attempts"] + 1})
            ctx = Ctx(scope=tid, origin="task", title=task["title"], web=bool(task.get("web", 1)), cfg=cfg,
                      autonomy=task["autonomy"], cancelled=lambda: tid in self.cancel_flags,
                      permission_timeout=6 * 3600)
            sink: list = []
            if task["agent"] == "team":
                gen = team_run(ctx, task["goal"], [], task["plan"], lambda p: memory.update_task(tid, plan=p), sink)
            else:
                agent = AGENTS.get(task["agent"], AGENTS["general"])
                model, _ = await choose_model(cfg, agent.model_tier)
                if not model:
                    raise RuntimeError("The model server is not reachable.")
                msgs = [{"role": "system", "content": system_prompt(agent, cfg, task["goal"]) +
                         "\n\nYou are running as a background task: work autonomously to completion and "
                         "finish with a short report of what you did and the outcome."},
                        {"role": "user", "content": task["goal"]}]
                gen = run_agent(ctx, agent, model, msgs, sink)
            async for ev in gen:
                if ev["type"] in SKIP_EVENTS:
                    continue
                log(ev)
                if ev["type"] == "permission":
                    memory.update_task(tid, status="waiting")
                elif ev["type"] == "permission_result":
                    memory.update_task(tid, status="running")
            result = sink[-1] if sink else ""
            log({"type": "done", "result": result[:4000]})
            if task["schedule"] and task["schedule"].get("type") in ("interval", "daily"):
                memory.update_task(tid, status="scheduled", result=result, plan=None, attempts=0,
                                   next_run=next_run_for(task["schedule"]))
            else:
                memory.update_task(tid, status="done", result=result, next_run=None)
        except Cancelled:
            memory.update_task(tid, status="cancelled")
            memory.add_task_event(tid, {"type": "status", "state": "cancelled"})
        except Exception as e:  # noqa: BLE001
            attempts = (task or {}).get("attempts", 0) + 1
            if attempts < MAX_ATTEMPTS:
                wait = 60 * attempts
                memory.update_task(tid, status="scheduled", attempts=attempts, error=str(e)[:500],
                                   next_run=time.time() + wait)
                memory.add_task_event(tid, {"type": "error", "text": f"{e} (retrying from the last checkpoint in {wait // 60} min)"})
            else:
                memory.update_task(tid, status="failed", attempts=attempts, error=str(e)[:500])
                memory.add_task_event(tid, {"type": "error", "text": f"{e} (gave up after {MAX_ATTEMPTS} attempts)"})
        finally:
            self.running.pop(tid, None)
            self.cancel_flags.discard(tid)


TASKS = TaskManager()
