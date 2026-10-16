"""Supabase copies of the agent's local state, for hosts whose disk is wiped on restart.

With `AEROPT_DURABLE_STATE=supabase` (Hugging Face Spaces), two local stores
keep working exactly as they do on disk and are also kept in Supabase
(the novelty ledger is not one of them any more: it lives only in `public.served_items`):

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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger("jee_tutor.durable_state")

DIAGRAM_BUCKET = os.getenv("AEROPT_DIAGRAM_BUCKET", "diagrams")
_PAGE = 1000

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


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
# The ledger's only home is `public.served_items` in the user-data project (there is no
# local SQLite copy any more); retrieval/store.py keeps a per-learner in-process cache.
# So this part is always on whenever the user DB is configured, not gated by enabled().
_LEDGER_COLUMNS = ("user_id", "content_id", "concept_id", "content_type", "purpose",
                   "fingerprint", "served_at", "times")


def _ledger_db() -> bool:
    return bool(os.getenv("SUPABASE_URL", "").strip()) and os.getenv("AEROPT_LEDGER", "").strip().lower() != "memory"


def load_served(user_id: str) -> Optional[list[Dict[str, Any]]]:
    """A learner's saved ledger rows; None when the user DB could not be read."""
    if not _ledger_db() or not user_id:
        return [] if not _ledger_db() else None
    rows: list[Dict[str, Any]] = []
    start = 0
    try:
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
                return rows
            start += _PAGE
    except Exception as exc:  # noqa: BLE001
        logger.warning("ledger load failed for a learner: %s", str(exc)[:200])
        return None


def save_served(rows: list[Dict[str, Any]]) -> None:
    """Write ledger rows through to the user DB in the background (a turn never waits)."""
    if _ledger_db() and rows:
        _background(_upsert_served, [{c: r.get(c) for c in _LEDGER_COLUMNS} for r in rows])


def _upsert_served(rows: list[Dict[str, Any]]) -> None:
    _client().table("served_items").upsert(rows, on_conflict="user_id,content_id").execute()


def forget_served(user_id: str, concept_id: Optional[str]) -> None:
    if _ledger_db() and user_id:
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
