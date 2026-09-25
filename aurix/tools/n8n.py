"""n8n integration: list workflows, run them by webhook, and create a new
simple workflow (Webhook trigger -> Respond to Webhook), built from plain
parameters rather than asking the model to write n8n's node/connection JSON
directly. Small models handle a few string arguments reliably; raw n8n
workflow JSON is not something a 2B/4B model should be trusted to construct.
"""
import httpx

from .. import settings


def _cfg():
    s = settings.load()
    base = (s.get("n8n_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("n8n is not set up. Add your n8n URL (and API key) in Settings.")
    return base, s.get("n8n_api_key") or ""


def _headers(key: str) -> dict:
    return {"X-N8N-API-KEY": key, "Content-Type": "application/json"} if key else {"Content-Type": "application/json"}


def n8n_list_workflows() -> str:
    try:
        base, key = _cfg()
        if not key:
            return ("Error: listing workflows needs an n8n API key (Settings). "
                    "You can still run or create a workflow without it.")
        r = httpx.get(f"{base}/api/v1/workflows", headers=_headers(key), timeout=20)
        r.raise_for_status()
        rows = [f"- {w['name']} ({'active' if w.get('active') else 'inactive'}, id {w['id']})"
                for w in r.json().get("data", [])]
        return "\n".join(rows) or "No workflows found."
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def n8n_run_webhook(path: str, data: dict | None = None) -> str:
    """Trigger a workflow's Webhook node. `path` is the webhook path, e.g. 'lead-scraper'."""
    try:
        base, _ = _cfg()
        url = path if path.startswith("http") else f"{base}/webhook/{path.lstrip('/')}"
        r = httpx.post(url, json=data or {}, timeout=120)
        return f"HTTP {r.status_code}\n{r.text[:4000]}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def n8n_create_workflow(name: str, webhook_path: str, response_text: str,
                        activate: bool = True) -> str:
    """Create a simple n8n workflow: a Webhook trigger that replies with
    response_text. Good for a quick connectivity test, or as a starting point
    the user can extend by hand in the n8n editor. Not for complex workflows:
    this builds exactly one trigger and one response node."""
    try:
        base, key = _cfg()
        if not key:
            return "Error: creating a workflow needs an n8n API key (Settings)."
        webhook_path = webhook_path.strip().strip("/") or "aurix-test"
        body = {
            "name": name.strip() or "AURIX test workflow",
            "nodes": [
                {"id": "trigger", "name": "Webhook", "type": "n8n-nodes-base.webhook",
                 "typeVersion": 2, "position": [240, 300],
                 "parameters": {"path": webhook_path, "httpMethod": "GET", "responseMode": "responseNode"}},
                {"id": "respond", "name": "Respond to Webhook", "type": "n8n-nodes-base.respondToWebhook",
                 "typeVersion": 1, "position": [520, 300],
                 "parameters": {"respondWith": "text", "responseBody": response_text}},
            ],
            "connections": {"Webhook": {"main": [[{"node": "Respond to Webhook", "type": "main", "index": 0}]]}},
            "settings": {"executionOrder": "v1"},
        }
        r = httpx.post(f"{base}/api/v1/workflows", headers=_headers(key), json=body, timeout=30)
        if r.status_code >= 300:
            return f"Error: n8n returned HTTP {r.status_code}\n{r.text[:1000]}"
        wf = r.json()
        wf_id = wf.get("id")
        note = ""
        if activate and wf_id:
            ar = httpx.post(f"{base}/api/v1/workflows/{wf_id}/activate", headers=_headers(key), timeout=20)
            note = " and activated" if ar.status_code < 300 else f" (activation failed: HTTP {ar.status_code})"
        test_url = f"{base}/webhook/{webhook_path}"
        return (f"Created{note}: '{wf['name']}' (id {wf_id}).\n"
                f"Test it: GET {test_url}\n"
                f"It should respond with: {response_text}")
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
