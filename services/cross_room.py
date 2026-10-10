"""What the student did in the *other* rooms, for the tutor's prompt.

Long-term memory (`learner_memory`) and the progress digest are already shared by
every tutor. What was not shared is the recent conversation: the chat tutor did
not know the student had just been in a Study Room lesson on SI units, and the
Study Room teacher did not know about the doubt they asked in chat ten minutes
ago. `recent_activity` gives each tutor a short, read-only digest of both.

Deterministic, no model. Every read is best-effort: a failure returns "".
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from db.supabase_client import db

logger = logging.getLogger("jee_tutor.cross_room")

_STUDY_SESSIONS = 3     # recent Study Room sessions listed
_OTHER_SESSIONS = 4     # recent conversations (other than this one) scanned
_TURNS_PER_SESSION = 4  # newest turns read from each
_MAX_TURNS = 8          # turns shown in total
_CLIP = 220             # characters kept per turn


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _CLIP else text[: _CLIP - 1].rstrip() + "…"


def _study_line(s: Dict[str, Any]) -> str:
    name = s.get("concept_name") or s.get("concept_id") or "a topic"
    served = int(s.get("questions_served") or 0)
    parts = [f"- {name}"]
    if s.get("chapter"):
        parts.append(f"({s['chapter']})")
    parts.append(f"reached {s.get('current_phase') or 'learn'}")
    if served:
        parts.append(f"{int(s.get('questions_correct') or 0)}/{served} correct")
    parts.append(f"outcome: {s.get('outcome')}" if s.get("outcome") else "still open")
    return " ".join(parts)


def recent_activity(user_id: str, current_session_id: str = "") -> str:
    """A few lines on recent Study Room sessions and recent exchanges in other
    conversations. Empty when there is nothing, or nothing could be read."""
    if not user_id:
        return ""

    try:
        study = db.list_user_study_sessions(user_id, limit=_STUDY_SESSIONS) or []
    except Exception:
        logger.debug("study session read failed", exc_info=True)
        study = []
    study_ids = set()
    try:
        rows = (
            db.client.table("study_sessions")
            .select("agent_session_id")
            .eq("user_id", user_id)
            .order("started_at", desc=True)
            .limit(20)
            .execute()
            .data
        ) or []
        study_ids = {r["agent_session_id"] for r in rows if r.get("agent_session_id")}
    except Exception:
        logger.debug("study session ids read failed", exc_info=True)

    turns: List[str] = []
    try:
        sessions = db.list_user_sessions(user_id, limit=_OTHER_SESSIONS + 1) or []
    except Exception:
        logger.debug("session list read failed", exc_info=True)
        sessions = []
    for s in sessions:
        sid = s.get("session_id")
        if not sid or sid == current_session_id:
            continue
        room = "Study Room" if sid in study_ids else "Chat"
        try:
            rows = db.read_recent_chat_turns(sid, n=_TURNS_PER_SESSION) or []
        except Exception:
            continue
        for r in rows:
            if r.get("agent_used") == "diagnostic" or not (r.get("content") or "").strip():
                continue
            who = "Student" if r.get("role") == "user" else "Tutor"
            turns.append(f"  [{room}] {who}: {_clip(r['content'])}")
        if len(turns) >= _MAX_TURNS:
            break

    parts: List[str] = []
    if study:
        parts.append("Recent Study Room sessions (newest first):\n" + "\n".join(_study_line(s) for s in study))
    if turns:
        parts.append(
            "Recent exchanges in their other conversations (newest conversation first). "
            "Use this to connect to what they were just doing; do not repeat it back unprompted:\n"
            + "\n".join(turns[:_MAX_TURNS])
        )
    return "\n\n".join(parts)
