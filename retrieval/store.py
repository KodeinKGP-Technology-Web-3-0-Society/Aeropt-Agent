"""The local index: SQLite for structure and lexical search, .npy for dense vectors.

Two files, two lifetimes, on purpose:

  index/corpus.sqlite3   rebuilt from scratch by every ingest (content is derived)
  index/ledger.sqlite3   never rebuilt (what a student has already been served)

That split is what makes "don't show me the same question again" survive a corpus
rebuild. The ledger is the only table the retrieval path writes to.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from services import durable_state

ROOT = Path(__file__).resolve().parents[1]
INDEX_DIR = Path(os.getenv("AEROPT_INDEX_DIR", ROOT / ".local-run" / "index"))
CORPUS_DB = INDEX_DIR / "corpus.sqlite3"
LEDGER_DB = Path(os.getenv("AEROPT_LEDGER_PATH", INDEX_DIR / "ledger.sqlite3"))
VECTORS = INDEX_DIR / "vectors.npy"
CONCEPT_VECTORS = INDEX_DIR / "concept_vectors.npy"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS chapters (
  chapter_id  TEXT PRIMARY KEY,
  title       TEXT,
  volume      TEXT,
  unit        TEXT,
  jee_unit    TEXT,
  in_syllabus INTEGER,
  ord         INTEGER
);

CREATE TABLE IF NOT EXISTS concepts (
  concept_id  TEXT PRIMARY KEY,
  name        TEXT,
  definition  TEXT,
  chapter_id  TEXT,
  chapter     TEXT,
  unit        TEXT,
  in_syllabus INTEGER,
  ord         INTEGER,
  aliases     TEXT,
  source      TEXT,
  vec_row     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_concepts_chapter ON concepts(chapter_id);

CREATE TABLE IF NOT EXISTS concept_edges (
  concept_id TEXT,
  requires   TEXT,
  PRIMARY KEY (concept_id, requires)
);
CREATE INDEX IF NOT EXISTS ix_edges_requires ON concept_edges(requires);

CREATE TABLE IF NOT EXISTS chunks (
  content_id    TEXT PRIMARY KEY,
  vec_row       INTEGER,
  content_type  TEXT,
  subject       TEXT,
  chapter_id    TEXT,
  chapter       TEXT,
  unit          TEXT,
  concept_id    TEXT,
  secondary     TEXT,
  source_book   TEXT,
  source_ref    TEXT,
  license       TEXT,
  difficulty    INTEGER,
  question_type TEXT,
  target_exam   TEXT,
  in_syllabus   INTEGER,
  has_solution  INTEGER,
  is_question   INTEGER,
  heading       TEXT,
  core_text     TEXT,
  solution      TEXT,
  fingerprint   TEXT,
  token_len     INTEGER,
  structured    TEXT   -- questions: {"stem","figure","options":[{"label","text"}],"answer":{...}} (JSON)
);
CREATE INDEX IF NOT EXISTS ix_chunks_concept ON chunks(concept_id, content_type);
CREATE INDEX IF NOT EXISTS ix_chunks_chapter ON chunks(chapter_id, content_type);
CREATE INDEX IF NOT EXISTS ix_chunks_fp ON chunks(fingerprint);
CREATE INDEX IF NOT EXISTS ix_chunks_question ON chunks(is_question, concept_id);

CREATE TABLE IF NOT EXISTS chunk_concepts (
  content_id TEXT,
  concept_id TEXT,
  role       TEXT,
  PRIMARY KEY (content_id, concept_id)
);
CREATE INDEX IF NOT EXISTS ix_chunk_concepts_concept ON chunk_concepts(concept_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  body, heading, concept, tokenize='porter unicode61'
);
"""

LEDGER_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS served (
  user_id     TEXT,
  content_id  TEXT,
  concept_id  TEXT,
  content_type TEXT,
  purpose     TEXT,
  fingerprint TEXT,
  served_at   REAL,
  times       INTEGER DEFAULT 1,
  PRIMARY KEY (user_id, content_id)
);
CREATE INDEX IF NOT EXISTS ix_served_user ON served(user_id, served_at DESC);
CREATE INDEX IF NOT EXISTS ix_served_fp ON served(user_id, fingerprint);
CREATE INDEX IF NOT EXISTS ix_served_concept ON served(user_id, concept_id);
"""

_local = threading.local()


def _connect(path: Path, schema: str | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if schema and not read_only:
        conn.executescript(schema)
    return conn


def corpus(read_only: bool = True) -> sqlite3.Connection:
    """Per-thread read connection to the corpus index."""
    key = "corpus_ro" if read_only else "corpus_rw"
    conn = getattr(_local, key, None)
    if conn is None:
        conn = _connect(CORPUS_DB, None if read_only else SCHEMA, read_only=read_only)
        setattr(_local, key, conn)
    return conn


def ledger() -> sqlite3.Connection:
    conn = getattr(_local, "ledger", None)
    if conn is None:
        conn = _connect(LEDGER_DB, LEDGER_SCHEMA)
        setattr(_local, "ledger", conn)
    return conn


def index_exists() -> bool:
    return CORPUS_DB.exists() and VECTORS.exists()


# ── vectors ───────────────────────────────────────────────────────────────────
_vectors: np.ndarray | None = None
_concept_vectors: np.ndarray | None = None


def vectors() -> np.ndarray:
    global _vectors
    if _vectors is None:
        _vectors = np.load(VECTORS)
    return _vectors


def concept_vectors() -> np.ndarray:
    global _concept_vectors
    if _concept_vectors is None:
        _concept_vectors = np.load(CONCEPT_VECTORS)
    return _concept_vectors


def reset_caches() -> None:
    global _vectors, _concept_vectors
    _vectors = None
    _concept_vectors = None
    for key in ("corpus_ro", "corpus_rw", "ledger"):
        conn = getattr(_local, key, None)
        if conn is not None:
            conn.close()
            setattr(_local, key, None)


# ── meta ──────────────────────────────────────────────────────────────────────
def meta() -> dict[str, Any]:
    try:
        rows = corpus().execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, Any] = {}
    for row in rows:
        try:
            out[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            out[row["key"]] = row["value"]
    return out


# ── reads used by retrieval ───────────────────────────────────────────────────
def concept(concept_id: str) -> dict[str, Any] | None:
    row = corpus().execute(
        "SELECT * FROM concepts WHERE concept_id = ?", (concept_id,)
    ).fetchone()
    return dict(row) if row else None


def concepts_by_ids(ids: Sequence[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = corpus().execute(
        f"SELECT * FROM concepts WHERE concept_id IN ({marks})", tuple(ids)
    ).fetchall()
    order = {cid: i for i, cid in enumerate(ids)}
    return sorted((dict(r) for r in rows), key=lambda r: order.get(r["concept_id"], 999))


def all_concepts() -> list[dict[str, Any]]:
    return [dict(r) for r in corpus().execute("SELECT * FROM concepts ORDER BY ord, concept_id")]


def prerequisites(concept_id: str, max_depth: int = 2) -> list[dict[str, Any]]:
    """Concepts this one depends on, closest first, breadth-first to max_depth."""
    seen: dict[str, int] = {}
    frontier = [concept_id]
    for hop in range(1, max_depth + 1):
        if not frontier:
            break
        marks = ",".join("?" * len(frontier))
        rows = corpus().execute(
            f"SELECT concept_id, requires FROM concept_edges WHERE concept_id IN ({marks})",
            tuple(frontier),
        ).fetchall()
        nxt: list[str] = []
        for row in rows:
            dep = row["requires"]
            if dep == concept_id or dep in seen:
                continue
            seen[dep] = hop
            nxt.append(dep)
        frontier = nxt
    if not seen:
        return []
    details = {c["concept_id"]: c for c in concepts_by_ids(list(seen))}
    out = [
        {
            "concept_id": cid,
            "concept_name": details.get(cid, {}).get("name", cid),
            "hop_count": hop,
        }
        for cid, hop in seen.items()
    ]
    out.sort(key=lambda r: r["hop_count"])
    return out


def dependents(concept_id: str, limit: int = 12) -> list[str]:
    rows = corpus().execute(
        "SELECT concept_id FROM concept_edges WHERE requires = ? LIMIT ?",
        (concept_id, limit),
    ).fetchall()
    return [r["concept_id"] for r in rows]


def chunks_by_id_prefix(prefix: str, limit: int = 40) -> list[dict[str, Any]]:
    """Rows whose content_id starts with `prefix`, in id order (paper, then question)."""
    rows = corpus().execute(
        "SELECT * FROM chunks WHERE content_id LIKE ? ESCAPE '\\' ORDER BY content_id LIMIT ?",
        (prefix.replace("_", "\\_") + "%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def chunks_by_ids(ids: Sequence[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = corpus().execute(
        f"SELECT * FROM chunks WHERE content_id IN ({marks})", tuple(ids)
    ).fetchall()
    order = {cid: i for i, cid in enumerate(ids)}
    return sorted((dict(r) for r in rows), key=lambda r: order.get(r["content_id"], 10**6))


# ── ledger (novelty) ──────────────────────────────────────────────────────────
def mark_served(
    user_id: str,
    items: Iterable[dict[str, Any]],
    purpose: str = "served",
) -> int:
    rows = [
        (
            user_id,
            it.get("content_id"),
            it.get("concept_id"),
            it.get("content_type"),
            purpose,
            it.get("fingerprint"),
            time.time(),
        )
        for it in items
        if it.get("content_id")
    ]
    if not user_id or not rows:
        return 0
    conn = ledger()
    durable_state.hydrate_ledger(conn, user_id)
    with conn:
        conn.executemany(
            """
            INSERT INTO served (user_id, content_id, concept_id, content_type, purpose,
                                fingerprint, served_at, times)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id, content_id) DO UPDATE SET
                served_at = excluded.served_at,
                purpose   = excluded.purpose,
                times     = served.times + 1
            """,
            rows,
        )
    durable_state.copy_served(conn, user_id, (row[1] for row in rows))
    return len(rows)


def _user_ledger(user_id: str) -> sqlite3.Connection:
    conn = ledger()
    durable_state.hydrate_ledger(conn, user_id)
    return conn


def served_ids(user_id: str, concept_ids: Sequence[str] | None = None) -> dict[str, float]:
    """content_id -> last served timestamp, optionally scoped to concepts."""
    if not user_id:
        return {}
    sql = "SELECT content_id, served_at FROM served WHERE user_id = ?"
    params: list[Any] = [user_id]
    if concept_ids:
        marks = ",".join("?" * len(concept_ids))
        sql += f" AND concept_id IN ({marks})"
        params.extend(concept_ids)
    return {r["content_id"]: r["served_at"] for r in _user_ledger(user_id).execute(sql, params)}


def served_fingerprints(user_id: str, concept_ids: Sequence[str] | None = None) -> set[str]:
    if not user_id:
        return set()
    sql = "SELECT DISTINCT fingerprint FROM served WHERE user_id = ? AND fingerprint IS NOT NULL"
    params: list[Any] = [user_id]
    if concept_ids:
        marks = ",".join("?" * len(concept_ids))
        sql += f" AND concept_id IN ({marks})"
        params.extend(concept_ids)
    return {r["fingerprint"] for r in _user_ledger(user_id).execute(sql, params) if r["fingerprint"]}


def served_count(user_id: str) -> int:
    row = _user_ledger(user_id).execute(
        "SELECT count(*) AS n FROM served WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row["n"]) if row else 0


def forget_served(user_id: str, concept_id: str | None = None) -> int:
    conn = ledger()
    durable_state.forget_served(user_id, concept_id)
    with conn:
        if concept_id:
            cur = conn.execute(
                "DELETE FROM served WHERE user_id = ? AND concept_id = ?", (user_id, concept_id)
            )
        else:
            cur = conn.execute("DELETE FROM served WHERE user_id = ?", (user_id,))
    return cur.rowcount


def unseen_question_count(
    user_id: str,
    concept_id: str,
    content_types: Sequence[str],
    *,
    require_solution: bool = False,
) -> tuple[int, int]:
    """(unseen, total) question chunks for one concept — the honest novelty budget.

    Search widens to the chapter when a concept runs dry, which is the right thing
    for relevance but hides the fact that the concept itself is exhausted. Counting
    it directly means the tutor can say "you have done every rolling-motion question
    I have" instead of quietly serving a neighbour's question as if it were new.
    """
    if not concept_id or not content_types:
        return 0, 0
    marks = ",".join("?" * len(content_types))
    sql = (
        f"SELECT content_id FROM chunks WHERE concept_id = ? AND content_type IN ({marks})"
    )
    params: list[Any] = [concept_id, *content_types]
    if require_solution:
        sql += " AND has_solution = 1"
    ids = [r["content_id"] for r in corpus().execute(sql, params)]
    if not ids:
        return 0, 0
    served = set(served_ids(user_id)) if user_id else set()
    return sum(1 for cid in ids if cid not in served), len(ids)
