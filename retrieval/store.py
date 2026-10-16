"""Retrieval storage: the Supabase retrieval Postgres (pgvector). No SQLite, no .npy.

Two databases, two owners, never mixed:

  RETRIEVAL_DATABASE_URL   the corpus — content, concepts, chapters, concept_edges,
                           chunk_concepts, syllabus_topics, retrieval_meta. Read-only at
                           runtime; written only by `make push` (local_ingest/push_index.py).
  user-data Supabase       the novelty ledger (`public.served_items`, via services/durable_state):
                           what each learner has already been served. Survives every corpus
                           rebuild because it is keyed by content_id, not by row position.

Rows come back in the shape the pipeline has always used (`concept_id`, `name`, `secondary`,
`structured` as JSON text, integer flags), so resolver/search/service read the same keys.

What is cached in-process, and why it is safe: the concept table (≈1.1k rows incl. vectors,
used by the resolver on every turn), chapters and syllabus topics. They change only when a
push lands, and a push bumps `retrieval_meta.built_at`, which is re-checked every
CACHE_CHECK_SECONDS. Content (13k+ rows, growing per subject) is never cached: dense and
lexical scoring run in SQL.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

import numpy as np

from retrieval import embedder

logger = logging.getLogger("aeropt.retrieval.store")

CACHE_CHECK_SECONDS = 300
LEDGER_REFRESH_SECONDS = 120

# The content row as the pipeline reads it. `c` is public.content.
CHUNK_COLUMNS = """
    c.content_id, c.content_type, c.subject, c.chapter_id, c.chapter, c.unit,
    c.primary_concept_id AS concept_id, c.secondary_concepts AS secondary,
    c.source_book, c.source_ref, c.license, c.difficulty, c.question_type, c.target_exam,
    coalesce(c.in_syllabus, true)::int AS in_syllabus, coalesce(c.has_solution, false)::int AS has_solution,
    coalesce(c.is_question, false)::int AS is_question, c.heading, c.core_text,
    coalesce(c.solution, '') AS solution, c.fingerprint, c.token_len, c.structured::text AS structured
"""


class RetrievalUnavailable(RuntimeError):
    """The retrieval database is not configured or not reachable."""


# ── connection ────────────────────────────────────────────────────────────────
_pool = None
_pool_lock = threading.Lock()


def database_url() -> str:
    return os.getenv("RETRIEVAL_DATABASE_URL", "").strip()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                url = database_url()
                if not url:
                    raise RetrievalUnavailable("RETRIEVAL_DATABASE_URL is not set")
                from psycopg2.pool import ThreadedConnectionPool

                _pool = ThreadedConnectionPool(1, int(os.getenv("RETRIEVAL_DB_POOL", "8")), url, connect_timeout=10)
    return _pool


@contextmanager
def _cursor():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    pool = _get_pool()
    conn = pool.getconn()
    broken = False
    try:
        conn.autocommit = True  # read path; works through Supabase's transaction pooler
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True
        raise
    finally:
        pool.putconn(conn, close=broken or bool(conn.closed))


def query(sql: str, params: Sequence[Any] | dict | None = None) -> list[dict[str, Any]]:
    """Run one read query against the retrieval database. Retries once on a dropped connection."""
    import psycopg2

    for attempt in (1, 2):
        try:
            with _cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()] if cur.description else []
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if attempt == 2:
                raise
    return []


def parse_vector(raw: Any) -> np.ndarray:
    """pgvector text ('[0.1,0.2,...]') -> float32 array."""
    if raw is None:
        return np.zeros(embedder.DIM, dtype=np.float32)
    if isinstance(raw, str):
        return np.fromstring(raw.strip("[]"), sep=",", dtype=np.float32)
    return np.asarray(raw, dtype=np.float32)


# ── embedding contract ────────────────────────────────────────────────────────
def verify_embedding_contract() -> dict[str, Any]:
    """Refuse to serve when stored vectors are not from embedder.MODEL_ID / embedder.DIM."""
    width = query(
        "select format_type(atttypid, atttypmod) as t from pg_attribute"
        " where attrelid = 'public.content'::regclass and attname = 'embedding'"
    )
    found = width[0]["t"] if width else None
    if found != f"vector({embedder.DIM})":
        raise embedder.EmbeddingMismatch(f"content.embedding is {found}, embedder is {embedder.DIM}-d")
    models = query(
        "select embed_model, count(*) as n from public.content where embedding is not null group by 1"
        " union all "
        "select embed_model, count(*) as n from public.concepts where embedding is not null group by 1"
    )
    foreign = {r["embed_model"]: r["n"] for r in models if r["embed_model"] != embedder.MODEL_ID}
    if foreign:
        raise embedder.EmbeddingMismatch(
            f"retrieval DB holds vectors from {foreign}; queries use {embedder.MODEL_ID}. Run `make push`."
        )
    return {"model": embedder.MODEL_ID, "dimension": embedder.DIM}


# ── in-process caches (concepts, chapters, topics) ────────────────────────────
_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def _version() -> str:
    rows = query("select value from public.retrieval_meta where key = 'built_at'")
    return rows[0]["value"] if rows else ""


def _fresh() -> dict[str, Any]:
    """The cache, reloaded when a push has landed since it was filled."""
    now = time.time()
    if _cache and now - _cache["checked_at"] < CACHE_CHECK_SECONDS:
        return _cache
    with _cache_lock:
        if _cache and now - _cache["checked_at"] < CACHE_CHECK_SECONDS:
            return _cache
        version = _version()
        if _cache and _cache.get("version") == version:
            _cache["checked_at"] = now
            return _cache
        rows = query(
            "select concept_id, concept_name as name, definition, chapter_id, chapter, unit, subject,"
            " coalesce(in_syllabus, true)::int as in_syllabus, ord, coalesce(aliases, '[]'::jsonb)::text as aliases,"
            " source, embedding::text as embedding from public.concepts order by ord nulls last, concept_id"
        )
        vecs = np.zeros((len(rows), embedder.DIM), dtype=np.float32)
        for i, r in enumerate(rows):
            vecs[i] = parse_vector(r.pop("embedding"))
            r["vec_row"] = i  # row in concept_vectors(); valid for this cache generation only
        chapters = query(
            "select chapter_id, subject, title, volume, unit, jee_unit, coalesce(in_syllabus, true)::int as in_syllabus,"
            " ord from public.chapters order by ord nulls last, chapter_id"
        )
        topics = query(
            "select subject, unit, unit_ord, topic, topic_ord, concept_id, chapter_id, created::int as created,"
            " material from public.syllabus_topics order by subject, unit_ord, topic_ord"
        )
        generation = _cache.get("generation", 0) + 1
        _cache.clear()
        _cache.update(
            version=version, checked_at=now, concepts=rows, concept_vectors=vecs,
            by_id={r["concept_id"]: r for r in rows}, chapters=chapters, topics=topics,
            generation=generation,
        )
        return _cache


def cache_generation() -> int:
    """Bumped whenever the concept cache is reloaded. Anything derived from concept rows or
    `vec_row` (the resolver's lexicon) rebuilds when this changes — it never calls back in here."""
    return _fresh()["generation"]


def reset_caches() -> None:
    with _cache_lock:
        _cache.clear()
    with _ledger_lock:
        _ledger.clear()


def index_exists() -> bool:
    """True when the retrieval database is configured, reachable and holds a corpus."""
    try:
        return bool(_fresh()["concepts"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("retrieval database unavailable: %s", str(exc)[:200])
        return False


def meta() -> dict[str, Any]:
    try:
        rows = query("select key, value from public.retrieval_meta")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, Any] = {}
    for row in rows:
        try:
            out[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            out[row["key"]] = row["value"]
    return out


# ── concepts / graph ──────────────────────────────────────────────────────────
def concept_vectors() -> np.ndarray:
    """(n_concepts, DIM), row i = all_concepts()[i] (`vec_row`)."""
    return _fresh()["concept_vectors"]


def concept(concept_id: str) -> dict[str, Any] | None:
    row = _fresh()["by_id"].get(concept_id)
    return dict(row) if row else None


def concepts_by_ids(ids: Sequence[str]) -> list[dict[str, Any]]:
    by_id = _fresh()["by_id"]
    return [dict(by_id[c]) for c in dict.fromkeys(ids) if c in by_id]


def all_concepts() -> list[dict[str, Any]]:
    return [dict(r) for r in _fresh()["concepts"]]


def chapters() -> list[dict[str, Any]]:
    return [dict(r) for r in _fresh()["chapters"]]


def syllabus_topics(subject: str | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in _fresh()["topics"] if not subject or r["subject"] == subject]


def prerequisites(concept_id: str, max_depth: int = 2) -> list[dict[str, Any]]:
    """Concepts this one depends on, closest first, breadth-first to max_depth (one query)."""
    if not concept_id:
        return []
    rows = query(
        """
        with recursive walk(concept_id, hop) as (
            select requires, 1 from public.concept_edges where concept_id = %(c)s
            union
            select e.requires, w.hop + 1 from public.concept_edges e
            join walk w on e.concept_id = w.concept_id where w.hop < %(d)s
        )
        select concept_id, min(hop) as hop_count from walk where concept_id <> %(c)s
        group by concept_id order by 2, 1
        """,
        {"c": concept_id, "d": max_depth},
    )
    by_id = _fresh()["by_id"]
    return [
        {"concept_id": r["concept_id"], "concept_name": by_id.get(r["concept_id"], {}).get("name", r["concept_id"]),
         "hop_count": int(r["hop_count"])}
        for r in rows
    ]


def dependents(concept_id: str, limit: int = 12) -> list[str]:
    rows = query("select concept_id from public.concept_edges where requires = %s limit %s", (concept_id, limit))
    return [r["concept_id"] for r in rows]


# ── content ───────────────────────────────────────────────────────────────────
def chunks_by_id_prefix(prefix: str, limit: int = 40) -> list[dict[str, Any]]:
    """Rows whose content_id starts with `prefix`, in id order (paper, then question)."""
    escaped = prefix.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
    return query(
        f"select {CHUNK_COLUMNS} from public.content c where c.content_id like %s order by c.content_id limit %s",
        (escaped + "%", limit),
    )


def chunks_by_ids(ids: Sequence[str]) -> list[dict[str, Any]]:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return []
    rows = query(f"select {CHUNK_COLUMNS} from public.content c where c.content_id = any(%s)", (ids,))
    order = {cid: i for i, cid in enumerate(ids)}
    return sorted(rows, key=lambda r: order.get(r["content_id"], 10**6))


def content_vectors(ids: Sequence[str]) -> dict[str, np.ndarray]:
    """content_id -> embedding, for a shortlist (MMR). Never called for a whole pool."""
    if not ids:
        return {}
    rows = query("select content_id, embedding::text as e from public.content where content_id = any(%s)", (list(ids),))
    return {r["content_id"]: parse_vector(r["e"]) for r in rows}


def questions_in_area(
    column: str,
    value: str,
    types: Sequence[str],
    difficulty: int,
    *,
    exclude_ids: Iterable[str] = (),
    keywords: Sequence[str] = (),
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Solved, in-syllabus questions of a chapter or JEE unit: ones naming the topic first, then
    nearest the target difficulty. Excluded ids are filtered in the database."""
    if column not in {"chapter_id", "unit"} or not value:
        return []
    patterns = [f"%{k.lower()}%" for k in keywords if k]
    return query(
        f"""select {CHUNK_COLUMNS} from public.content c
            where c.{column} = %(v)s and c.content_type = any(%(types)s)
              and c.has_solution and coalesce(c.in_syllabus, true)
              and not (c.content_id = any(%(ex)s))
            order by (lower(c.core_text) like any(%(kw)s)) desc,
                     abs(coalesce(c.difficulty, 2) - %(d)s), c.content_id
            limit %(n)s""",
        {"v": value, "types": list(types), "ex": list(exclude_ids), "kw": patterns or ["\x00"],
         "d": difficulty, "n": limit},
    )


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
    sql = "select content_id from public.content where primary_concept_id = %s and content_type = any(%s)"
    if require_solution:
        sql += " and has_solution"
    ids = [r["content_id"] for r in query(sql, (concept_id, list(content_types)))]
    if not ids:
        return 0, 0
    served = set(served_ids(user_id)) if user_id else set()
    return sum(1 for cid in ids if cid not in served), len(ids)


# ── ledger (novelty) — user-data Supabase, cached per learner ─────────────────
_ledger: dict[str, dict[str, Any]] = {}   # user_id -> {"loaded": ts, "rows": {content_id: row}}
_ledger_lock = threading.Lock()


def _user_rows(user_id: str) -> dict[str, dict[str, Any]]:
    from services import durable_state

    now = time.time()
    entry = _ledger.get(user_id)
    if entry and now - entry["loaded"] < LEDGER_REFRESH_SECONDS:
        return entry["rows"]
    with _ledger_lock:
        entry = _ledger.get(user_id)
        if entry and now - entry["loaded"] < LEDGER_REFRESH_SECONDS:
            return entry["rows"]
        saved = durable_state.load_served(user_id)
        rows = dict(entry["rows"]) if entry else {}
        if saved is not None:  # None = the user DB was unreachable: keep what this process knows
            for r in saved:
                prior = rows.get(r["content_id"])
                if prior is None or (r.get("served_at") or 0) >= (prior.get("served_at") or 0):
                    rows[r["content_id"]] = r
        _ledger[user_id] = {"loaded": now, "rows": rows}
        return rows


def mark_served(user_id: str, items: Iterable[dict[str, Any]], purpose: str = "served") -> int:
    from services import durable_state

    if not user_id:
        return 0
    rows = _user_rows(user_id)
    written = []
    with _ledger_lock:
        for it in items:
            cid = it.get("content_id")
            if not cid:
                continue
            prior = rows.get(cid)
            row = {
                "user_id": user_id, "content_id": cid, "concept_id": it.get("concept_id") or it.get("primary_concept_id"),
                "content_type": it.get("content_type"), "purpose": purpose, "fingerprint": it.get("fingerprint"),
                "served_at": time.time(), "times": (prior or {}).get("times", 0) + 1,
            }
            rows[cid] = row
            written.append(row)
    if written:
        durable_state.save_served(written)
    return len(written)


def served_ids(user_id: str, concept_ids: Sequence[str] | None = None) -> dict[str, float]:
    """content_id -> last served timestamp, optionally scoped to concepts."""
    if not user_id:
        return {}
    scope = set(concept_ids or ())
    return {
        cid: float(r.get("served_at") or 0.0)
        for cid, r in _user_rows(user_id).items()
        if not scope or r.get("concept_id") in scope
    }


def served_fingerprints(user_id: str, concept_ids: Sequence[str] | None = None) -> set[str]:
    if not user_id:
        return set()
    scope = set(concept_ids or ())
    return {
        r["fingerprint"] for r in _user_rows(user_id).values()
        if r.get("fingerprint") and (not scope or r.get("concept_id") in scope)
    }


def served_count(user_id: str) -> int:
    return len(_user_rows(user_id)) if user_id else 0


def forget_served(user_id: str, concept_id: str | None = None) -> int:
    from services import durable_state

    rows = _user_rows(user_id)
    with _ledger_lock:
        gone = [cid for cid, r in rows.items() if not concept_id or r.get("concept_id") == concept_id]
        for cid in gone:
            rows.pop(cid, None)
    durable_state.forget_served(user_id, concept_id)
    return len(gone)
