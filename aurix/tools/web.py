import html
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

UA = {"User-Agent": "Mozilla/5.0 (AURIX local assistant)"}


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def web_search(query: str) -> str:
    try:
        r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query},
                       headers=UA, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"Error: search failed ({e})"
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
    out = []
    for i, (href, title) in enumerate(links[:6]):
        if "uddg=" in href:  # DuckDuckGo redirect link -> real URL
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        snip = _clean(snippets[i]) if i < len(snippets) else ""
        out.append(f"{i + 1}. {_clean(title)}\n   {href}\n   {snip}")
    return "\n".join(out) or "No results (the search page may have blocked the request)."


def fetch_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "Error: only http(s) URLs are allowed"
    try:
        r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"Error: could not fetch page ({e})"
    text = r.text
    if "html" in r.headers.get("content-type", ""):
        text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</div>", "\n", text)
        text = _clean(text)
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text))
    return text[:12000]
