"""Permission manager: every tool call passes through here before it runs.

safe       -> runs immediately
sensitive  -> asks the user (a chat can allow it for the rest of the conversation;
              a task with autonomy "trusted" runs it without asking)
dangerous  -> asks the user every time, in chats and tasks alike

Pending requests are kept in one registry, so the UI can list and resolve
approvals from chats and from background tasks the same way."""
import asyncio
import time
import uuid

from . import settings

LEVELS = ("safe", "sensitive", "dangerous")


def level_of(tool: str) -> str:
    lvl = settings.load()["permissions"].get(tool, "dangerous")  # unknown tools count as dangerous
    return lvl if lvl in LEVELS else "dangerous"


class PermissionManager:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._allowed: dict[str, set[str]] = {}   # scope (conversation/task id) -> sensitive tools

    def auto_allowed(self, scope: str, tool: str, autonomy: str = "ask") -> bool:
        lvl = level_of(tool)
        if lvl == "safe":
            return True
        if lvl == "sensitive":
            return autonomy == "trusted" or tool in self._allowed.get(scope, set())
        return False

    def request(self, scope: str, tool: str, args: dict, level: str, origin: str, title: str = "") -> tuple[str, asyncio.Future]:
        pid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._pending[pid] = {"id": pid, "scope": scope, "tool": tool, "args": args, "level": level,
                              "origin": origin, "title": title, "created": time.time(), "future": fut}
        return pid, fut

    def pending(self) -> list[dict]:
        return [{k: v for k, v in p.items() if k != "future"} for p in self._pending.values()
                if not p["future"].done()]

    def resolve(self, pid: str, allow: bool, remember: bool = False) -> bool:
        p = self._pending.pop(pid, None)
        if p is None or p["future"].done():
            return False
        if allow and remember and level_of(p["tool"]) == "sensitive":
            self._allowed.setdefault(p["scope"], set()).add(p["tool"])
        p["future"].set_result(allow)
        return True

    def cancel_scope(self, scope: str) -> None:
        for pid, p in list(self._pending.items()):
            if p["scope"] == scope:
                self.cancel(pid)

    def cancel(self, pid: str) -> None:
        p = self._pending.pop(pid, None)
        if p and not p["future"].done():
            p["future"].set_result(False)


PERMISSIONS = PermissionManager()
