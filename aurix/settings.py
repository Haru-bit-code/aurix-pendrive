"""Runtime settings: defaults from config.json, user changes saved to data/settings.json."""
import copy
import json

from .config import CONFIG, ROOT

PATH = ROOT / "data" / "settings.json"

DEFAULTS = {
    "models": {"fast": "", "strong": "", "code": "", "vision": ""},  # exact ids; "" = config.json names
    "model_override": "",                        # "" = choose per agent
    "temperature": 0.7,
    "top_p": 0.8,
    "max_tokens": 2048,                          # 0 = no limit; a cap keeps the Mac cooler
    "thinking": False,                           # Qwen "thinking" mode
    "custom_instructions": "",
    "max_tool_steps": CONFIG["max_tool_steps"],
    "history_messages": CONFIG["history_messages"],
    "web_default": False,
    "memory_in_prompt": True,                    # add relevant long-term facts to every prompt
    "autonomy_default": "ask",                   # tasks: "ask" or "trusted" (dangerous always asks)
    "max_concurrent_tasks": 1,
    "review_rounds": 1,                          # team mode: fix-and-review cycles
    "browser_headless": False,                   # show the automation browser window
    "voice_reply": False,                        # read answers aloud
    "voice_lang": "en-US",
    "n8n_url": "",
    "n8n_api_key": "",
    "permissions": dict(CONFIG["permissions"]),
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if k not in base:
            continue
        out[k] = _merge(base[k], v) if isinstance(base[k], dict) and isinstance(v, dict) else v
    return out


def load() -> dict:
    try:
        return _merge(DEFAULTS, json.loads(PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULTS)


def save(new: dict) -> dict:
    merged = _merge(load(), new)
    merged["temperature"] = min(max(float(merged["temperature"]), 0.0), 2.0)
    merged["top_p"] = min(max(float(merged["top_p"]), 0.05), 1.0)
    merged["max_tokens"] = max(int(merged["max_tokens"]), 0)
    merged["max_tool_steps"] = min(max(int(merged["max_tool_steps"]), 1), 25)
    merged["history_messages"] = min(max(int(merged["history_messages"]), 0), 100)
    merged["max_concurrent_tasks"] = min(max(int(merged["max_concurrent_tasks"]), 1), 3)
    merged["review_rounds"] = min(max(int(merged["review_rounds"]), 0), 3)
    if merged["autonomy_default"] not in ("ask", "trusted"):
        merged["autonomy_default"] = "ask"
    merged["permissions"] = {k: (v if v in ("safe", "sensitive", "dangerous") else "dangerous")
                             for k, v in merged["permissions"].items()}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def reset() -> dict:
    PATH.unlink(missing_ok=True)
    return load()
