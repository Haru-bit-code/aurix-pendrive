"""Phase 9: detect this computer's hardware so the UI (and you) can see what AURIX
is running on and which settings suit it."""
import os
import platform
import shutil
import subprocess
import sys

from .config import ROOT


def _ram_gb() -> float | None:
    try:
        if platform.system() == "Windows":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS(); m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 1024 ** 3, 1)
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
            return round(int(out) / 1024 ** 3, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        return None


def _gpu() -> str:
    system = platform.system()
    if system == "Darwin":
        if platform.machine() == "arm64":
            try:
                chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
            except Exception:  # noqa: BLE001
                chip = "Apple Silicon"
            return f"{chip} GPU (Metal, shared memory)"
        return "Intel Mac (CPU / Metal)"
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            return f"NVIDIA {out} (CUDA)" if out else "NVIDIA GPU (CUDA)"
        except Exception:  # noqa: BLE001
            return "NVIDIA GPU (CUDA)"
    return "No NVIDIA GPU found (CPU or Vulkan)"


def report() -> dict:
    ram = _ram_gb()
    disk = shutil.disk_usage(ROOT)
    try:
        import playwright  # noqa: F401
        pw = True
    except ImportError:
        pw = False
    tips = []
    if ram and ram < 12:
        tips.append("Under 12 GB of RAM: set MAX_MODELS=1 in the launcher and prefer the 2B model.")
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        tips.append("MacBook Air has no fan: keep Max reply tokens around 1024 to 2048 and Thinking mode off.")
    if "No NVIDIA" in (g := _gpu()) and platform.system() == "Windows":
        tips.append("No NVIDIA GPU: use the Vulkan or CPU build of llama.cpp in bin\\win (see README).")
    return {
        "os": f"{platform.system()} {platform.release()}", "arch": platform.machine(),
        "cpu_cores": os.cpu_count(), "ram_gb": ram, "gpu": g,
        "python": sys.version.split()[0], "drive_free_gb": round(disk.free / 1024 ** 3, 1),
        "drive_total_gb": round(disk.total / 1024 ** 3, 1), "git": bool(shutil.which("git")),
        "playwright": pw, "tips": tips,
    }
