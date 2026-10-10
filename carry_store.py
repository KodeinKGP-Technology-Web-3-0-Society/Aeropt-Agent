"""File-backed carry state — survives agent restarts without DB schema changes.

Where the disk itself is wiped on restart, `services/durable_state.py` keeps a
Supabase copy that a fresh container reads back.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict

from services import durable_state

_SESSION_TTL_SECONDS = 2 * 60 * 60
_STORE_DIR = Path(os.getenv("AEROPT_CARRY_DIR", Path(__file__).resolve().parent / ".carry_sessions"))


def _file_path(session_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    return _STORE_DIR / f"{safe}.json"


def _prune() -> None:
    if not _STORE_DIR.exists():
        return
    cutoff = time.time() - _SESSION_TTL_SECONDS
    for file in _STORE_DIR.glob("*.json"):
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink(missing_ok=True)
        except OSError:
            pass


def _write_local(session_id: str, carry: Dict[str, Any]) -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    _file_path(session_id).write_text(
        json.dumps({"carry": carry, "last_seen": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_carry(session_id: str) -> Dict[str, Any]:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    _prune()
    path = _file_path(session_id)
    if not path.exists():
        saved = durable_state.load_carry(session_id, _SESSION_TTL_SECONDS)
        if saved is None:
            return {}
        _write_local(session_id, saved)
        return saved
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("carry") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_carry(session_id: str, carry: Dict[str, Any]) -> None:
    _write_local(session_id, carry)
    durable_state.save_carry(session_id, carry)


def release_carry(session_id: str) -> None:
    _file_path(session_id).unlink(missing_ok=True)
    durable_state.release_carry(session_id)
