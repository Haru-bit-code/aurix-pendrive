import platform
import subprocess
import sys

from ..config import WORKSPACE

TIMEOUT = 60


def _run(args, shell=False) -> str:
    try:
        p = subprocess.run(args, cwd=WORKSPACE, shell=shell, capture_output=True,
                           text=True, timeout=TIMEOUT, errors="replace")
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        return f"exit code {p.returncode}\n{out.strip()}"[:8000]
    except subprocess.TimeoutExpired:
        return f"Error: stopped after {TIMEOUT} seconds"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def run_python(code: str) -> str:
    script = WORKSPACE / "_aurix_run.py"
    script.write_text(code, encoding="utf-8")
    try:
        return _run([sys.executable, script.name])
    finally:
        script.unlink(missing_ok=True)


def run_shell(command: str) -> str:
    return _run(command, shell=True)


def open_application(name: str) -> str:
    system = platform.system()
    try:
        if system == "Darwin":
            p = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=15)
        elif system == "Windows":
            p = subprocess.run(["cmd", "/c", "start", "", name], capture_output=True, text=True, timeout=15)
        else:
            p = subprocess.run(["xdg-open", name], capture_output=True, text=True, timeout=15)
        return f"Opened {name}" if p.returncode == 0 else f"Error: {p.stderr.strip() or 'could not open'}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
