"""Computer control (Phase 6): app scripting, opening URLs/paths, git."""
import platform
import shlex
import subprocess

from ..config import WORKSPACE
from .files import _safe

SYSTEM = platform.system()


def run_applescript(script: str) -> str:
    """Control Mac apps (Notes, Music, Finder, Mail drafts...). macOS only."""
    if SYSTEM != "Darwin":
        return "Error: AppleScript is only available on macOS"
    try:
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
        out = (p.stdout or "").strip()
        return out or ("Done." if p.returncode == 0 else f"Error: {p.stderr.strip()}")
    except subprocess.TimeoutExpired:
        return "Error: the script took longer than 60 seconds"


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "Error: only http(s) links can be opened"
    import webbrowser
    return f"Opened {url}" if webbrowser.open(url) else "Error: no browser available"


def open_path(path: str) -> str:
    """Open a workspace file or folder in its default app."""
    p = _safe(path)
    if not p.exists():
        return f"Error: '{path}' does not exist in the workspace"
    cmd = {"Darwin": ["open", str(p)], "Windows": ["explorer", str(p)]}.get(SYSTEM, ["xdg-open", str(p)])
    subprocess.Popen(cmd)
    return f"Opened {p.relative_to(WORKSPACE)}"


GIT_ALLOWED = {"init", "status", "log", "diff", "add", "commit", "branch", "checkout", "switch",
               "restore", "show", "rev-parse", "stash", "tag", "remote"}


def git(args: str, repo: str = ".") -> str:
    parts = shlex.split(args)
    if not parts or parts[0] not in GIT_ALLOWED:
        return f"Error: allowed git commands: {', '.join(sorted(GIT_ALLOWED))}"
    if parts[0] == "remote" and len(parts) > 1 and parts[1] not in ("-v", "show"):
        return "Error: only 'git remote -v' is allowed"
    cwd = _safe(repo)
    try:
        p = subprocess.run(["git", *parts], cwd=cwd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return "Error: git is not installed on this computer"
    out = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()
    return (out or "Done.")[:8000]
