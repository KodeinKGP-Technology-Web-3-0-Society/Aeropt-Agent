"""Graded follow-up: prerequisite suggestions, quiz advancement, and next-material cards.

Selection of a suggested item belongs to the Main Tutor and its open_material
tool. These helpers do not route free text through an intent classifier.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.supabase_client import db
from retrieval.curriculum_graph import graph_client
from models.state import TutorState
from utils.presentation import filter_picker_suggestions


_CTYPE_ALIASES: Dict[str, str] = {
    "pyq": "pyq",
    "past year question": "pyq",
    "past year paper": "pyq",
    "practice": "practice",
    "practice question": "practice",
    "example": "example",
    "solved example": "example",
    "worked example": "example",
    "theory": "theory",
}


def _normalize_ctype(raw: str) -> str:
    """Return a canonical lowercase content_type from any raw DB value."""
    key = (raw or "").lower().strip()
    return _CTYPE_ALIASES.get(key, key)


def _surface_weak_prereq(state: TutorState) -> Optional[Dict[str, Any]]:
    """
    After an incorrect graded attempt, find the weakest prerequisite concept
    and surface it to the student.

    Returns a state-update dict or None if no weak prereq found.
    """
    prereqs: List[Dict[str, Any]] = state.get("prereq_concept_states") or []
    
    from services.prerequisites import find_weak_direct_prereq
    top = find_weak_direct_prereq(prereqs)
    if not top:
        return None

    concept_name = top.get("concept_name") or top.get("concept_id", "that concept")
    concept_id = top.get("concept_id", "")

    message = (
        f"Don't worry — it happens! It looks like **{concept_name}** "
        f"might be a gap that's making this harder.\n\n"
        f"Would you like to cover **{concept_name}** first before trying again? "
        f"Reply **yes** to start, or just ask your next question to continue."
    )

    return {
        "response": message,
        "agent_used": "follow_up",
        "pending_prereq_concept_id": concept_id,
        "follow_up_suggestions": [],
        "error": None,
    }


# ── Advance a recorded quiz attempt ──────────────────────────────────────────


def _handle_quiz_advance(state: TutorState) -> Dict[str, Any]:
    """
    Runs after Diagnostic grades one question of an on-demand quiz batch.
    Advances to the next question, or finishes the quiz when all are done.
    Proficiency was already updated by Diagnostic; this only drives the prompt.
    """
    chunks: List[Dict[str, Any]] = state.get("quiz_batch_chunks") or []
    current_index = state.get("quiz_batch_index", 0)
    next_index = current_index + 1
    total = len(chunks)

    if next_index < total:
        msg = (
            f"Recorded. Use the **Correct** or **Incorrect** buttons for "
            f"**Question {next_index + 1}**."
        )
        return {
            "response": msg,
            "agent_used": "quiz",
            "quiz_batch_mode": True,
            "quiz_batch_index": next_index,
            "awaiting_graded_response": True,
            "error": None,
        }

    # All questions graded — close out the batch.
    msg = (
        "That completes the quiz — your proficiency scores have been updated for "
        "each question. Ask me anything next, or request another quiz!"
    )
    return {
        "response": msg,
        "agent_used": "quiz",
        "quiz_batch_mode": False,
        "quiz_batch_index": 0,
        "quiz_batch_chunks": [],
        "awaiting_graded_response": False,
        "follow_up_suggestions": [],
        "error": None,
    }


# ── Main node ─────────────────────────────────────────────────────────────────

def follow_up_node(state: TutorState) -> Dict[str, Any]:
    # ── On-demand quiz batch in progress → advance to next question ───────────
    if state.get("quiz_batch_mode") and state.get("has_graded_outcome"):
        try:
            return _handle_quiz_advance(state)
        except Exception as exc:
            return {"error": f"Follow-Up Agent failed (quiz advance): {exc}"}

    # ── Incorrect graded attempt → surface weak prerequisite ─────────────────
    if state.get("has_graded_outcome") and not state.get("is_correct", True):
        try:
            result = _surface_weak_prereq(state)
            if result:
                return result
            # No weak prereqs found — fall through to normal suggestions
        except Exception as exc:
            return {"error": f"Follow-Up Agent failed (prereq surfacing): {exc}"}

    # ── Normal follow-up suggestions ─────────────────────────────────────────
    user_id = state["user_id"]
    primary_id = state.get("primary_concept_id", "")
    target_exam = state.get("target_exam", "JEE_ADVANCED")

    try:
        if not primary_id:
            return {"follow_up_suggestions": []}

        # Brief acknowledgement when the student just answered correctly
        ack_response: Optional[str] = None
        if state.get("has_graded_outcome") and state.get("is_correct"):
            ack_response = "Well done! Here are some suggestions for what to try next:"

        # 1. Get content IDs already seen by this user (all-time deduplication)
        #    Also exclude any IDs already shown as follow-up suggestions in this
        #    session so the student never sees the same card twice.
        seen_ids = db.read_seen_content_ids(user_id)
        session_seen: List[str] = state.get("seen_follow_up_ids") or []
        all_excluded = set(seen_ids) | set(session_seen)

        # 2. Difficulty ceiling: match or slightly exceed the student's proficiency
        target_state = state.get("target_concept_state") or {}
        proficiency = target_state.get("proficiency_score", 0.5)
        difficulty_max = max(1, min(4, int(proficiency * 4) + 1))

        # 3. Neo4j: find unseen, appropriately-difficult content for the concept
        candidate_ids = graph_client().get_content_ids_for_follow_up(
            concept_id=primary_id,
            seen_ids=list(all_excluded),
            difficulty_max=difficulty_max,
            limit=10,
        )

        if not candidate_ids:
            candidate_ids = graph_client().get_content_ids_for_follow_up(
                concept_id=primary_id,
                seen_ids=list(all_excluded),
                difficulty_max=5,
                limit=10,
            )

        # 4. Supabase: fetch full metadata for candidates
        candidates = db.read_content_by_ids(candidate_ids[:6])

        # 5. Pick 2-3 for card picker: theory + example only (Phase 2.5 Option A)
        TYPE_PRIORITY = {"example": 0, "theory": 1}
        candidates = [
            c
            for c in candidates
            if _normalize_ctype(c.get("content_type", "")) in {"theory", "example"}
        ]
        candidates.sort(
            key=lambda c: (
                TYPE_PRIORITY.get(_normalize_ctype(c.get("content_type", "theory")), 4),
                c.get("difficulty") or 3,
            )
        )
        suggestions = []
        for c in candidates[:3]:
            # Preserve every field the DB returns so downstream agents and the
            # frontend never lose image, latex, or solution data.
            suggestion = dict(c)
            suggestion["content_type"] = _normalize_ctype(c.get("content_type", ""))
            suggestion["preview"]      = (c.get("core_text") or "")[:150]
            suggestions.append(suggestion)

        suggestions = filter_picker_suggestions(suggestions)

        # The turn service retains the IDs of suggestions shown in this session.
        result: Dict[str, Any] = {
            "follow_up_suggestions": suggestions,
            # The graded cycle is complete — clear the awaiting flag so the
            # frontend no longer shows "Reply 1/0" after this turn.
            "awaiting_graded_response": False,
            "error": None,
        }
        if ack_response:
            result["response"] = ack_response
            result["agent_used"] = "follow_up"

        return result

    except Exception as exc:
        return {"error": f"Follow-Up Agent failed: {exc}"}
