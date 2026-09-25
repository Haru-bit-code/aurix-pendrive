"""SQLite storage: conversations, messages, routing log, long-term memory facts,
documents (RAG chunks), and background tasks with checkpointed steps."""
import json
import os
import sqlite3
import time
import uuid

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT,
    content TEXT, tool_calls TEXT, tool_call_id TEXT, name TEXT, meta TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, message TEXT,
    agent TEXT, model TEXT, reason TEXT, manual INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, source TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, path TEXT, chars INTEGER, chunks INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER, seq INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, title TEXT, goal TEXT, agent TEXT, autonomy TEXT, status TEXT,
    schedule TEXT, next_run REAL, plan TEXT, result TEXT, error TEXT, attempts INTEGER DEFAULT 0, web INTEGER DEFAULT 1,
    created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, event TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id, id);
"""


def db() -> sqlite3.Connection:
    # The pendrive is exFAT. On macOS, SQLite's normal file locking fails there
    # ("attempt to write a readonly database"), so on Mac/Linux we use the
    # no-locking VFS and keep the rollback journal in memory. AURIX is the only
    # process using this database, so skipping file locks is safe.
    if os.name == "posix":
        con = sqlite3.connect(DB_PATH.as_uri() + "?vfs=unix-none", uri=True, timeout=10)
        con.execute("PRAGMA journal_mode=MEMORY")
    else:
        con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


with db() as _c:
    _c.executescript(SCHEMA)


# ---------- conversations ----------
def new_conversation(title: str) -> str:
    cid = uuid.uuid4().hex[:16]
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO conversations VALUES (?,?,?,?)", (cid, title[:80], now, now))
    return cid


def conversation_exists(cid: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone() is not None


def list_conversations() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC")
        return [dict(r) for r in rows]


def delete_conversation(cid: str) -> None:
    with db() as c:
        c.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        c.execute("DELETE FROM conversations WHERE id=?", (cid,))


def add_message(cid: str, msg: dict, meta: dict | None = None) -> None:
    content = msg.get("content") or ""
    if isinstance(content, list):  # multimodal message: keep only the text parts
        content = " ".join(p.get("text", "") for p in content if p.get("type") == "text") + " [image]"
    with db() as c:
        c.execute(
            "INSERT INTO messages (conversation_id, role, content, tool_calls, tool_call_id, name,"
            " meta, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (cid, msg["role"], content,
             json.dumps(msg["tool_calls"]) if msg.get("tool_calls") else None,
             msg.get("tool_call_id"), msg.get("name"),
             json.dumps(meta) if meta else None, time.time()))
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))


def get_messages(cid: str) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,))
        out = []
        for r in rows:
            m = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                m["tool_calls"] = json.loads(r["tool_calls"])
            if r["tool_call_id"]:
                m["tool_call_id"] = r["tool_call_id"]
            if r["name"]:
                m["name"] = r["name"]
            if r["meta"]:
                m["meta"] = json.loads(r["meta"])
            out.append(m)
        return out


def history_for_model(cid: str, limit: int) -> list[dict]:
    """Past user turns and final assistant answers only (tool traffic is left out)."""
    msgs = [{"role": m["role"], "content": m["content"]} for m in get_messages(cid)
            if m["role"] == "user" or (m["role"] == "assistant" and not m.get("tool_calls")
                                       and m["content"])]
    return msgs[-limit:] if limit else []


def log_route(cid: str, message: str, agent: str, model: str, reason: str, manual: bool) -> None:
    with db() as c:
        c.execute("INSERT INTO routing_log (conversation_id, message, agent, model, reason, manual,"
                  " created_at) VALUES (?,?,?,?,?,?,?)",
                  (cid, message, agent, model, reason, int(manual), time.time()))


# ---------- long-term memory facts ----------
def add_fact(text: str, source: str = "chat") -> int:
    with db() as c:
        cur = c.execute("INSERT INTO facts (text, source, created_at) VALUES (?,?,?)",
                        (text.strip()[:500], source, time.time()))
        return cur.lastrowid


def list_facts() -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM facts ORDER BY id DESC")]


def delete_fact(fid: int) -> bool:
    with db() as c:
        return c.execute("DELETE FROM facts WHERE id=?", (fid,)).rowcount > 0


# ---------- documents ----------
def add_document(name: str, path: str, chunks: list[str], chars: int) -> int:
    with db() as c:
        c.execute("DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE path=?)", (path,))
        c.execute("DELETE FROM documents WHERE path=?", (path,))
        doc_id = c.execute("INSERT INTO documents (name, path, chars, chunks, created_at) VALUES (?,?,?,?,?)",
                           (name, path, chars, len(chunks), time.time())).lastrowid
        c.executemany("INSERT INTO chunks (document_id, seq, text) VALUES (?,?,?)",
                      [(doc_id, i, t) for i, t in enumerate(chunks)])
        return doc_id


def list_documents() -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM documents ORDER BY id DESC")]


def delete_document(doc_id: int) -> bool:
    with db() as c:
        c.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
        return c.execute("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount > 0


def all_chunks() -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT chunks.id, chunks.text, chunks.seq, documents.name FROM chunks "
            "JOIN documents ON documents.id = chunks.document_id")]


# ---------- tasks ----------
TASK_FIELDS = ("title", "goal", "agent", "autonomy", "status", "schedule", "next_run", "plan",
               "result", "error", "attempts", "web")


def create_task(**f) -> str:
    tid = uuid.uuid4().hex[:12]
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO tasks (id, title, goal, agent, autonomy, status, schedule, next_run, plan,"
                  " result, error, attempts, web, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, f.get("title"), f.get("goal"), f.get("agent", "team"), f.get("autonomy", "ask"),
                   f.get("status", "queued"), json.dumps(f.get("schedule")) if f.get("schedule") else None,
                   f.get("next_run"), None, None, None, 0, int(bool(f.get("web", True))), now, now))
    return tid


def update_task(tid: str, **f) -> None:
    sets, vals = [], []
    for k, v in f.items():
        if k in TASK_FIELDS:
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k in ("plan", "schedule") and v is not None else v)
    if not sets:
        return
    with db() as c:
        c.execute(f"UPDATE tasks SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, time.time(), tid))


def _task_row(r) -> dict:
    d = dict(r)
    for k in ("plan", "schedule"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get_task(tid: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return _task_row(r) if r else None


def list_tasks() -> list[dict]:
    with db() as c:
        return [_task_row(r) for r in c.execute("SELECT * FROM tasks ORDER BY created_at DESC")]


def delete_task(tid: str) -> None:
    with db() as c:
        c.execute("DELETE FROM task_events WHERE task_id=?", (tid,))
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))


def add_task_event(tid: str, event: dict) -> int:
    with db() as c:
        return c.execute("INSERT INTO task_events (task_id, event, created_at) VALUES (?,?,?)",
                         (tid, json.dumps(event), time.time())).lastrowid


def task_events(tid: str, after: int = 0) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT id, event, created_at FROM task_events WHERE task_id=? AND id>? ORDER BY id",
                         (tid, after))
        return [{"id": r["id"], "t": r["created_at"], **json.loads(r["event"])} for r in rows]
