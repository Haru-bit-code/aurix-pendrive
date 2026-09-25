"""AURIX HTTP API + web UI."""
import json
import re
from contextlib import asynccontextmanager

import mimetypes

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, doctor, llm, memory, rag, settings
from .agents import AGENTS, TEAM_LABEL
from .config import WEB_DIR, WORKSPACE
from .engine import run_turn
from .permissions import PERMISSIONS
from .tasks import TASKS, describe
from .tools import TOOLS
from .tools.files import _safe


@asynccontextmanager
async def lifespan(_app):
    TASKS.start()
    yield


app = FastAPI(title="AURIX", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def always_fresh_ui(request, call_next):
    """Make the browser re-check the UI files on every load, so an update to
    index.html / app.js / style.css is never mixed with a stale cached copy."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None
    agent: str = "auto"
    model: str = ""
    web: bool = False
    images: list[str] = []


class PermissionIn(BaseModel):
    allow: bool
    remember: bool = False


class ModelAction(BaseModel):
    model: str


class TaskIn(BaseModel):
    goal: str
    title: str | None = None
    agent: str = "team"
    autonomy: str | None = None
    web: bool = True
    schedule: dict | None = None


class FactIn(BaseModel):
    text: str


class PathIn(BaseModel):
    path: str


# ---------- status ----------
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/status")
async def status():
    online = await llm.is_online()
    models = []
    if online:
        try:
            models = await llm.models_detail()
        except Exception:  # noqa: BLE001
            pass
    agents = [{"name": a.name, "label": a.label, "tools": a.tools, "tier": a.model_tier} for a in AGENTS.values()]
    agents.append({"name": "team", "label": TEAM_LABEL, "tools": [], "tier": "strong"})
    return {"llama_online": online, "models": models, "workspace": str(WORKSPACE), "version": __version__,
            "agents": agents, "pending_permissions": len(PERMISSIONS.pending()),
            "tasks_active": sum(1 for t in memory.list_tasks() if t["status"] in ("running", "waiting")),
            "tools": [{"name": t.name, "description": t.description, "web": t.needs_web} for t in TOOLS.values()]}


@app.get("/api/doctor")
async def get_doctor():
    return doctor.report()


# ---------- models ----------
@app.post("/api/models/load")
async def load_model(body: ModelAction):
    return await llm.set_loaded(body.model, True)


@app.post("/api/models/unload")
async def unload_model(body: ModelAction):
    return await llm.set_loaded(body.model, False)


# ---------- settings ----------
@app.get("/api/settings")
async def get_settings():
    return settings.load()


@app.put("/api/settings")
async def put_settings(body: dict):
    return settings.save(body)


@app.delete("/api/settings")
async def reset_settings():
    return settings.reset()


# ---------- workspace files ----------
@app.get("/api/files")
async def files(path: str = "."):
    try:
        p = _safe(path)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    if not p.is_dir():
        raise HTTPException(404, "Folder not found")
    items = []
    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if item.name.startswith("._") or item.name in (".DS_Store", "_aurix_run.py"):
            continue
        items.append({"name": item.name, "path": str(item.relative_to(WORKSPACE)),
                      "dir": item.is_dir(), "size": None if item.is_dir() else item.stat().st_size})
    rel = "." if p == WORKSPACE else str(p.relative_to(WORKSPACE))
    return {"path": rel, "items": items}


@app.get("/api/file")
async def file(path: str):
    try:
        p = _safe(path)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    if not p.is_file():
        raise HTTPException(404, "File not found")
    data = p.read_bytes()[:200_000]
    if b"\x00" in data[:4000]:
        return {"path": path, "binary": True, "size": p.stat().st_size, "content": ""}
    return {"path": path, "binary": False, "size": p.stat().st_size,
            "content": data.decode("utf-8", errors="replace")}


# ---------- knowledge: documents + long-term memory ----------
@app.get("/api/docs")
async def docs():
    return memory.list_documents()


@app.post("/api/docs")
async def upload_doc(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\- ]+", "_", file.filename or "document")[:120]
    dest = WORKSPACE / "knowledge" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    try:
        return rag.ingest(dest, name=f"knowledge/{name}")
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read {name}: {e}") from e


@app.post("/api/docs/from-workspace")
async def doc_from_workspace(body: PathIn):
    try:
        p = _safe(body.path)
        return rag.ingest(p, name=str(p.relative_to(WORKSPACE)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e


@app.delete("/api/docs/{doc_id}")
async def delete_doc(doc_id: int):
    return {"deleted": memory.delete_document(doc_id)}


@app.get("/api/docs/search")
async def search_docs(q: str):
    return rag.retrieve(q, k=6)


@app.get("/api/memory")
async def facts():
    return memory.list_facts()


@app.post("/api/memory")
async def add_fact(body: FactIn):
    if not body.text.strip():
        raise HTTPException(400, "Memory is empty")
    return {"id": memory.add_fact(body.text, source="you")}


@app.delete("/api/memory/{fid}")
async def delete_fact(fid: int):
    return {"deleted": memory.delete_fact(fid)}


# ---------- tasks ----------
@app.get("/api/tasks")
async def tasks():
    return [{**t, "schedule_text": describe(t["schedule"]), "plan": None,
             "steps": len((t["plan"] or {}).get("steps", [])),
             "steps_done": sum(1 for s in (t["plan"] or {}).get("steps", []) if s.get("result") is not None)}
            for t in memory.list_tasks()]


@app.post("/api/tasks")
async def create_task(body: TaskIn):
    if not body.goal.strip():
        raise HTTPException(400, "Describe the goal for the task")
    agent = body.agent if body.agent in AGENTS or body.agent == "team" else "team"
    autonomy = body.autonomy or settings.load()["autonomy_default"]
    if autonomy not in ("ask", "trusted"):
        autonomy = "ask"
    return {"id": TASKS.create(body.goal.strip(), body.title, agent, autonomy, body.web, body.schedule)}


@app.get("/api/tasks/{tid}")
async def task(tid: str, after: int = 0):
    t = memory.get_task(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return {**t, "schedule_text": describe(t["schedule"]), "events": memory.task_events(tid, after)}


@app.post("/api/tasks/{tid}/{action}")
async def task_action(tid: str, action: str):
    if not memory.get_task(tid):
        raise HTTPException(404, "Task not found")
    {"cancel": TASKS.cancel, "resume": TASKS.resume, "run": TASKS.run_now}.get(
        action, lambda _t: (_ for _ in ()).throw(HTTPException(400, "Unknown action")))(tid)
    return {"ok": True}


@app.delete("/api/tasks/{tid}")
async def delete_task(tid: str):
    TASKS.delete(tid)
    return {"deleted": tid}


# ---------- permissions (chats and tasks) ----------
@app.get("/api/permissions")
async def permissions():
    return PERMISSIONS.pending()


@app.post("/api/permission/{pid}")
async def permission(pid: str, body: PermissionIn):
    if not PERMISSIONS.resolve(pid, body.allow, body.remember):
        raise HTTPException(404, "This request already expired")
    return {"ok": True}


# ---------- conversations ----------
@app.get("/api/conversations")
async def conversations():
    return memory.list_conversations()


@app.get("/api/conversations/{cid}")
async def conversation(cid: str):
    if not memory.conversation_exists(cid):
        raise HTTPException(404, "Conversation not found")
    return memory.get_messages(cid)


@app.delete("/api/conversations/{cid}")
async def delete(cid: str):
    PERMISSIONS.cancel_scope(cid)
    memory.delete_conversation(cid)
    return {"deleted": cid}


@app.post("/api/chat")
async def chat(body: ChatIn):
    if not body.message.strip() and not body.images:
        raise HTTPException(400, "Message is empty")

    async def events():
        async for ev in run_turn(body.conversation_id, body.message or "Describe this image.", body.agent,
                                 body.web, body.model, body.images):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- live preview: serve workspace files as real web content ----------
# So an agent-written .html (with its linked .css/.js/images) can be opened and
# actually render, instead of only being readable as text via /api/file.
@app.get("/workspace/{file_path:path}")
async def workspace_file(file_path: str):
    try:
        p = _safe(file_path)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    if not p.is_file():
        raise HTTPException(404, "File not found")
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return Response(content=p.read_bytes(), media_type=ctype)


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
