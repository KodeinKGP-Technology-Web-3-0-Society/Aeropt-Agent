"""Supabase copies of the agent's local state, for hosts whose disk is wiped on restart.

With `AEROPT_DURABLE_STATE=supabase` (Hugging Face Spaces), three local stores
keep working exactly as they do on disk and are also kept in Supabase:

- the novelty ledger (`retrieval/store.py`, SQLite) → `public.served_items`
- carry sessions (`carry_store.py`, JSON files)    → `public.carry_sessions`
- compiled diagrams (`services/diagrams.py`, SVG)  → Storage bucket `diagrams`

Local stays the read path, so a turn costs no extra round trips: writes are
copied in a single background thread (FIFO, so a save followed by a release
lands in that order), and Supabase is read only when local has nothing — the
first ledger read for a learner after a restart, a carry or a diagram the
current container has not seen. Every call fails open: if Supabase is down the
local copy still serves, and the next restart simply starts from what was saved.
Tables are created by `db/migrations/016_durable_agent_state.sql`.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger("jee_tutor.durable_state")

DIAGRAM_BUCKET = os.getenv("AEROPT_DIAGRAM_BUCKET", "diagrams")
_PAGE = 1000

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_hydrated: set[str] = set()
_hydrate_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("AEROPT_DURABLE_STATE", "").strip().lower() == "supabase"


def _client():
    from db.supabase_client import db

    return db.client


def _background(fn: Callable[..., Any], *args: Any) -> None:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="durable-state")

    def run() -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 — a lost copy must not fail a turn
            logger.warning("durable copy %s failed: %s", fn.__name__, str(exc)[:200])

    _executor.submit(run)


# ── novelty ledger ────────────────────────────────────────────────────────────
_LEDGER_COLUMNS = ("user_id", "content_id", "concept_id", "content_type", "purpose",
                   "fingerprint", "served_at", "times")


def hydrate_ledger(conn: sqlite3.Connection, user_id: str) -> None:
    """Load a learner's saved ledger into local SQLite once per process."""
    if not enabled() or not user_id or user_id in _hydrated:
        return
    with _hydrate_lock:
        if user_id in _hydrated:
            return
        try:
            rows: list[Dict[str, Any]] = []
            start = 0
            while True:
                page = (
                    _client().table("served_items")
                    .select(",".join(_LEDGER_COLUMNS))
                    .eq("user_id", user_id)
                    .range(start, start + _PAGE - 1)
                    .execute()
                ).data or []
                rows.extend(page)
                if len(page) < _PAGE:
                    break
                start += _PAGE
        except Exception as exc:  # noqa: BLE001
            logger.warning("ledger hydrate failed for a learner: %s", str(exc)[:200])
            return
        if rows:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO served (user_id, content_id, concept_id, content_type, purpose,
                                        fingerprint, served_at, times)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, content_id) DO UPDATE SET
                        served_at = max(served.served_at, excluded.served_at),
                        times     = max(served.times, excluded.times)
                    """,
                    [tuple(r.get(c) for c in _LEDGER_COLUMNS) for r in rows],
                )
        _hydrated.add(user_id)


def copy_served(conn: sqlite3.Connection, user_id: str, content_ids: Iterable[str]) -> None:
    """Copy the just-written ledger rows (with their updated `times`) to Supabase."""
    if not enabled():
        return
    ids = [cid for cid in content_ids if cid]
    if not user_id or not ids:
        return
    marks = ",".join("?" * len(ids))
    rows = [
        {c: r[c] for c in _LEDGER_COLUMNS}
        for r in conn.execute(
            f"SELECT {','.join(_LEDGER_COLUMNS)} FROM served WHERE user_id = ? AND content_id IN ({marks})",
            (user_id, *ids),
        )
    ]
    if rows:
        _background(_upsert_served, rows)


def _upsert_served(rows: list[Dict[str, Any]]) -> None:
    _client().table("served_items").upsert(rows, on_conflict="user_id,content_id").execute()


def forget_served(user_id: str, concept_id: Optional[str]) -> None:
    if enabled() and user_id:
        _background(_delete_served, user_id, concept_id)


def _delete_served(user_id: str, concept_id: Optional[str]) -> None:
    query = _client().table("served_items").delete().eq("user_id", user_id)
    if concept_id:
        query = query.eq("concept_id", concept_id)
    query.execute()


# ── carry sessions ────────────────────────────────────────────────────────────
def load_carry(session_id: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    since = (datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)).isoformat()
    try:
        rows = (
            _client().table("carry_sessions")
            .select("carry")
            .eq("session_id", session_id)
            .gte("updated_at", since)
            .limit(1)
            .execute()
        ).data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("carry load failed: %s", str(exc)[:200])
        return None
    return (rows[0].get("carry") or {}) if rows else None


def save_carry(session_id: str, carry: Dict[str, Any]) -> None:
    if enabled():
        _background(_upsert_carry, session_id, carry)


def _upsert_carry(session_id: str, carry: Dict[str, Any]) -> None:
    _client().table("carry_sessions").upsert(
        {"session_id": session_id, "carry": carry, "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="session_id",
    ).execute()


def release_carry(session_id: str) -> None:
    if enabled():
        _background(_delete_carry, session_id)


def _delete_carry(session_id: str) -> None:
    _client().table("carry_sessions").delete().eq("session_id", session_id).execute()


# ── diagrams ──────────────────────────────────────────────────────────────────
def save_diagram(diagram_id: str, svg: bytes) -> None:
    if enabled():
        _background(_upload_diagram, diagram_id, svg)


def _upload_diagram(diagram_id: str, svg: bytes) -> None:
    _client().storage.from_(DIAGRAM_BUCKET).upload(
        f"svg/{diagram_id}.svg",
        svg,
        {"content-type": "image/svg+xml", "upsert": "true"},
    )


def load_diagram(diagram_id: str) -> Optional[bytes]:
    if not enabled():
        return None
    try:
        return _client().storage.from_(DIAGRAM_BUCKET).download(f"svg/{diagram_id}.svg")
    except Exception as exc:  # noqa: BLE001 — not found is the usual reason
        logger.info("diagram %s not in storage: %s", diagram_id, str(exc)[:120])
        return None


def drain() -> None:
    """Wait for queued copies (tests, and a clean shutdown)."""
    global _executor
    with _executor_lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=False)


def reset_for_tests() -> None:
    drain()
    _hydrated.clear()
