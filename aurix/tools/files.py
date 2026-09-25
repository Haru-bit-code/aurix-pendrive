from pathlib import Path

from ..config import WORKSPACE

MAX_READ = 20000


def _safe(path: str) -> Path:
    """Resolve a path and refuse anything outside the workspace."""
    p = (WORKSPACE / (path or ".")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise PermissionError("path is outside the workspace")
    return p


def list_files(path: str = ".") -> str:
    try:
        p = _safe(path)
        if not p.is_dir():
            return f"Error: '{path}' is not a folder"
        rows = []
        for item in sorted(p.iterdir()):
            if item.name.startswith("._") or item.name == ".DS_Store":
                continue
            rel = item.relative_to(WORKSPACE)
            rows.append(f"{rel}/" if item.is_dir() else f"{rel}  ({item.stat().st_size} bytes)")
        return "\n".join(rows) or "(empty folder)"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def read_file(path: str) -> str:
    try:
        p = _safe(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ:
            text = text[:MAX_READ] + f"\n...[truncated, {len(text)} chars total]"
        return text
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    try:
        p = _safe(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {p.relative_to(WORKSPACE)}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
