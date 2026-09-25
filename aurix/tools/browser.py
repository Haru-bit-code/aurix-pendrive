"""Browser automation with Playwright (Phase 6).

One visible browser window is kept open between tool calls. Playwright's sync API
must stay on one thread, so every call runs on a dedicated single worker thread.
On first use it tries the installed Google Chrome; if Chrome is missing it
downloads Playwright's own Chromium once (about 150 MB, needs internet)."""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from .. import settings
from ..config import WORKSPACE

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")
_state = {"pw": None, "browser": None, "page": None}


def _launch():
    from playwright.sync_api import sync_playwright
    headless = bool(settings.load().get("browser_headless"))
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=headless)
    except Exception:  # noqa: BLE001  Chrome not installed -> Playwright's Chromium
        try:
            browser = pw.chromium.launch(headless=headless)
        except Exception:  # noqa: BLE001
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           check=True, capture_output=True, timeout=900)
            browser = pw.chromium.launch(headless=headless)
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    _state.update(pw=pw, browser=browser, page=page)


def _page():
    if _state["page"] is None or _state["page"].is_closed():
        if _state["browser"] is not None:
            try:
                _state["page"] = _state["browser"].new_page()
                return _state["page"]
            except Exception:  # noqa: BLE001
                pass
        _launch()
    return _state["page"]


def _run(fn):
    try:
        return _pool.submit(fn).result(timeout=180)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _text(page, limit=6000) -> str:
    txt = page.evaluate("() => document.body ? document.body.innerText : ''")
    return f"{page.title()} ({page.url})\n\n{txt[:limit]}"


def browser_open(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    def go():
        p = _page()
        p.goto(url, wait_until="domcontentloaded", timeout=45000)
        return _text(p)
    return _run(go)


def browser_read() -> str:
    return _run(lambda: _text(_page()))


def browser_click(text: str) -> str:
    """Click a link or button by its visible text."""
    def go():
        p = _page()
        target = p.get_by_role("button", name=text).or_(p.get_by_role("link", name=text)).or_(p.get_by_text(text, exact=False))
        target.first.click(timeout=10000)
        p.wait_for_load_state("domcontentloaded", timeout=20000)
        return "Clicked. " + _text(p, 3000)
    return _run(go)


def browser_fill(field: str, value: str) -> str:
    """Type into an input found by its label, placeholder or name."""
    def go():
        p = _page()
        loc = p.get_by_label(field).or_(p.get_by_placeholder(field)).or_(p.locator(f"[name='{field}']"))
        loc.first.fill(value, timeout=10000)
        return f"Filled '{field}'."
    return _run(go)


def browser_screenshot(name: str = "screenshot.png") -> str:
    def go():
        path = WORKSPACE / (name if name.endswith(".png") else name + ".png")
        _page().screenshot(path=str(path), full_page=False)
        return f"Saved {path.relative_to(WORKSPACE)}"
    return _run(go)


def browser_close() -> str:
    def go():
        if _state["browser"]:
            _state["browser"].close()
        if _state["pw"]:
            _state["pw"].stop()
        _state.update(pw=None, browser=None, page=None)
        return "Browser closed."
    return _run(go)
