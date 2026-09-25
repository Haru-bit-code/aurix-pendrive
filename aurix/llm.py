"""Client for llama-server's OpenAI-compatible API (router mode)."""
import json
from typing import AsyncIterator

import httpx

from .config import CONFIG

BASE = CONFIG["llama_url"].rstrip("/")


def _real(model_id: str) -> bool:
    # Skip macOS "._name" metadata files that exFAT drives collect next to real files
    return not model_id.startswith(".")


async def models_detail() -> list[dict]:
    """[{id, status: loaded|loading|unloaded, failed}] from the router."""
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{BASE}/models")
        r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        if not _real(m["id"]):
            continue
        st = m.get("status") or {}
        out.append({"id": m["id"], "status": st.get("value", "unknown"),
                    "failed": bool(st.get("failed"))})
    return out


async def list_models() -> list[str]:
    return [m["id"] for m in await models_detail()]


async def model_status(model: str) -> str:
    try:
        for m in await models_detail():
            if m["id"] == model:
                return m["status"]
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


async def set_loaded(model: str, load: bool) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/models/{'load' if load else 'unload'}", json={"model": model})
        return {"ok": r.status_code == 200, "detail": r.text[:300]}


async def is_online() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(f"{BASE}/health")).status_code == 200
    except Exception:  # noqa: BLE001
        return False


FALLBACK = {"fast": ("fast", "strong"), "strong": ("strong", "fast"),
            "code": ("code", "strong", "fast"), "vision": ("vision", "strong", "fast")}


def pick_model(available: list[str], tier: str, chosen: dict) -> str | None:
    """Exact id from settings first, then the config.json name fragment, then anything."""
    if not available:
        return None
    for t in FALLBACK.get(tier, (tier, "strong", "fast")):
        if chosen.get(t) in available:
            return chosen[t]
        want = CONFIG["models"].get(t, "").lower()
        if want:
            for m in available:
                if want in m.lower():
                    return m
    return available[0]


async def stream_chat(model: str, messages: list[dict], tools: list[dict],
                      params: dict) -> AsyncIterator[dict]:
    """Yield token/reasoning events while streaming, then one 'end' event with the
    assembled message and llama-server's timings."""
    body = {"model": model, "messages": messages, "stream": True, **params}
    if tools:
        body["tools"] = tools
    content, calls, timings = [], {}, {}
    # Long read timeout: in router mode the first request also loads the model.
    timeout = httpx.Timeout(connect=10, read=900, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", f"{BASE}/v1/chat/completions", json=body) as r:
            if r.status_code != 200:
                detail = (await r.aread()).decode(errors="replace")[:500]
                raise RuntimeError(f"llama-server returned {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("timings"):
                    timings = chunk["timings"]
                if not chunk.get("choices"):
                    continue
                delta = chunk["choices"][0].get("delta") or {}
                if delta.get("reasoning_content"):
                    yield {"type": "reasoning", "text": delta["reasoning_content"]}
                if delta.get("content"):
                    content.append(delta["content"])
                    yield {"type": "token", "text": delta["content"]}
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0),
                                            {"id": "", "type": "function",
                                             "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
    tool_calls = [calls[i] for i in sorted(calls)]
    for i, tc in enumerate(tool_calls):
        tc["id"] = tc["id"] or f"call_{i}"
    yield {"type": "end", "content": "".join(content), "tool_calls": tool_calls, "timings": timings}


async def complete_json(model: str, messages: list[dict], schema: dict, params: dict | None = None) -> dict:
    """One non-streaming call whose output is constrained to a JSON schema (llama.cpp
    grammar). Small models plan and review far more reliably this way."""
    body = {"model": model, "messages": messages, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}},
            **(params or {})}
    body.pop("max_tokens", None)
    timeout = httpx.Timeout(connect=10, read=900, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{BASE}/v1/chat/completions", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"llama-server returned {r.status_code}: {r.text[:300]}")
        text = r.json()["choices"][0]["message"].get("content") or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
