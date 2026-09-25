"""Long-term memory facts and document search (Phase 5)."""
from .. import memory, rag
from ..config import WORKSPACE
from .files import _safe


def remember(fact: str) -> str:
    fid = memory.add_fact(fact, source="agent")
    return f"Saved to long-term memory (#{fid})."


def recall(query: str) -> str:
    hits = rag.bm25(query, memory.list_facts(), k=8)
    return "\n".join(f"#{h['id']}: {h['text']}" for h in hits) or "Nothing relevant in long-term memory."


def forget(fact_id: int) -> str:
    return "Deleted." if memory.delete_fact(int(fact_id)) else f"No memory with id {fact_id}."


def search_documents(query: str) -> str:
    hits = rag.retrieve(query, k=5)
    if not hits:
        return "No matching passages in the knowledge base."
    return "\n\n".join(f"[{h['name']} #{h['seq']}] {h['text']}" for h in hits)


def add_document(path: str) -> str:
    p = _safe(path)
    if not p.is_file():
        return f"Error: '{path}' is not a file in the workspace"
    info = rag.ingest(p, name=str(p.relative_to(WORKSPACE)))
    return f"Added {info['name']} to the knowledge base ({info['chunks']} passages)."
