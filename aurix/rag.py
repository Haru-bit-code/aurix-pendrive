"""Phase 5: document ingestion and retrieval.

Retrieval uses BM25 keyword ranking in pure Python instead of an embedding model:
no extra model to load, no extra heat, instant on thousands of chunks. The
retrieve() interface is the only thing to change to add embeddings later."""
import math
import re
from collections import Counter
from pathlib import Path

from . import memory

TEXT_EXT = {".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".csv", ".yaml",
            ".yml", ".toml", ".html", ".css", ".sql", ".sh", ".java", ".go", ".rs", ".c", ".cpp", ".h",
            ".ini", ".cfg", ".log", ".xml", ".rst", ".tex"}
WORD = re.compile(r"[a-z0-9_]+")
STOP = set("a an the and or of to in on for is are was were be been it this that with as at by from "
           "what which who how why when where i you he she we they my your our their me do does did "
           "not no yes can could should would will just about into than then there here".split())


def tokens(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    if ext == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    if ext in TEXT_EXT or not ext:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"unsupported file type '{ext}' (use text, code, Markdown, PDF or DOCX)")


def chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):  # prefer to cut at a paragraph or sentence boundary
            cut = max(text.rfind("\n\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in out if c]


def ingest(path: Path, name: str | None = None) -> dict:
    text = extract_text(path)
    parts = chunk(text)
    if not parts:
        raise ValueError("no readable text found in the file")
    doc_id = memory.add_document(name or path.name, str(path), parts, len(text))
    return {"id": doc_id, "name": name or path.name, "chunks": len(parts), "chars": len(text)}


def bm25(query: str, items: list[dict], key: str = "text", k: int = 5, k1: float = 1.4, b: float = 0.75):
    q = tokens(query)
    if not q or not items:
        return []
    docs = [tokens(it[key]) for it in items]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1
    df = Counter(t for d in docs for t in set(d))
    scored = []
    for it, d in zip(items, docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(it, score=round(s, 2)) for s, it in scored[:k]]


def retrieve(query: str, k: int = 4) -> list[dict]:
    return bm25(query, memory.all_chunks(), k=k)


def relevant_facts(query: str, k: int = 6) -> list[dict]:
    facts = memory.list_facts()
    if len(facts) <= k:
        return facts
    hits = bm25(query, facts, k=k)
    return hits or facts[:3]
