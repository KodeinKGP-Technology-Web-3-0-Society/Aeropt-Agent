"""Shared turn, session, carry, and grading services for Aeropt agent entry points.

Used by both CLI (`main.py`) and HTTP API (`api_server.py`) to eliminate logic drift.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from agents.context_manager import context_manager_node
from db.supabase_client import db
from models.state import TutorState

logger = logging.getLogger("jee_tutor.turn_service")

# ── Cross-turn carry state ────────────────────────────────────────────────────
#
# LangGraph starts fresh each invocation. A small set of fields must survive
# from one turn to the next so that:
#   - Diagnostic knows which concept was being practised (primary_concept_id)
#   - Follow-Up can surface weak prereqs (prereq_concept_states)
#   - Orchestrator can inject the pending prereq on confirmation (pending_prereq_concept_id)
#   - Follow-Up can show the previously offered suggestions (follow_up_suggestions)
#
_CARRY_KEYS = [
    "primary_concept_id",
    "secondary_concept_ids",
    "content_type_requested",
    "prior_session_summary",  # loaded once; keep it for all subsequent turns
    "seen_follow_up_ids",  # session-scoped dedup for follow-up suggestions
    "problem_state",
]

# File-carry keys that must NEVER be merged into LangGraph TutorState.
_CARRY_EXCLUDE_FROM_STATE = {
    "presentation_by_turn",
}

# Ephemeral practice/PYQ fields — must not bleed into unrelated new queries.
_PRACTICE_EPHEMERAL_KEYS = (
    "awaiting_graded_response",
    "retrieved_chunks",
    "prereq_concept_states",
    "secondary_concept_states",
    "target_concept_state",
)

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


def normalize_content_type(raw: str) -> str:
    """Return a canonical lowercase content_type from any raw DB or LLM value."""
    key = (raw or "").lower().strip()
    return _CTYPE_ALIASES.get(key, key)


def clear_stale_practice_carry(carry: Dict[str, Any]) -> None:
    """Drop in-flight practice/PYQ carry when the student sends a new free-form message.

    Prevents replaying the same question every turn when grading was skipped.
    """
    for key in _PRACTICE_EPHEMERAL_KEYS:
        carry.pop(key, None)
    if normalize_content_type(str(carry.get("content_type_requested") or "")) in {
        "practice",
        "pyq",
    }:
        carry.pop("content_type_requested", None)


def update_carry(
    carry: Dict[str, Any],
    result: TutorState,
    was_graded: bool = False,
) -> None:
    """Update carry state from the completed turn's result.

    was_graded=True  -> the student just submitted a "1"/"0" (or ANSWER::) answer,
                        so the practice/PYQ cycle is complete; clear the awaiting
                        flag and all chunk carry-state.
    """
    for key in _CARRY_KEYS:
        val = result.get(key)  # type: ignore[call-overload]
        if val is not None:
            if key == "content_type_requested":
                carry[key] = normalize_content_type(val)
            else:
                carry[key] = val

    # ── awaiting_graded_response lifecycle ────────────────────────────────────
    new_awaiting = result.get("awaiting_graded_response")  # type: ignore[call-overload]
    if new_awaiting is True:
        carry["awaiting_graded_response"] = True
    elif was_graded or new_awaiting is False:
        carry.pop("awaiting_graded_response", None)

    # ── retrieved_chunks / prereq / target_state — only while awaiting ────────
    if carry.get("awaiting_graded_response"):
        for k in (
            "retrieved_chunks",
            "prereq_concept_states",
            "secondary_concept_states",
            "target_concept_state",
        ):
            v = result.get(k)  # type: ignore[call-overload]
            if v is not None:
                carry[k] = v
    else:
        carry.pop("retrieved_chunks", None)
        carry.pop("prereq_concept_states", None)
        carry.pop("secondary_concept_states", None)
        carry.pop("target_concept_state", None)

    # ── on-demand quiz batch lifecycle ────────────────────────────────────────
    new_quiz_mode = result.get("quiz_batch_mode")  # type: ignore[call-overload]
    if new_quiz_mode is True:
        carry["quiz_batch_mode"] = True
        new_chunks = result.get("quiz_batch_chunks")  # type: ignore[call-overload]
        if new_chunks is not None:
            carry["quiz_batch_chunks"] = new_chunks
        new_idx = result.get("quiz_batch_index")  # type: ignore[call-overload]
        if new_idx is not None:
            carry["quiz_batch_index"] = new_idx
        q_chunks = carry.get("quiz_batch_chunks") or []
        q_idx = carry.get("quiz_batch_index", 0)
        if 0 <= q_idx < len(q_chunks):
            carry["retrieved_chunks"] = [q_chunks[q_idx]]
    elif new_quiz_mode is False:
        carry.pop("quiz_batch_mode", None)
        carry.pop("quiz_batch_index", None)
        carry.pop("quiz_batch_chunks", None)

    # ── pending_prereq_concept_id ─────────────────────────────────────────────
    new_pending = result.get("pending_prereq_concept_id")  # type: ignore[call-overload]
    if new_pending:
        carry["pending_prereq_concept_id"] = new_pending
    else:
        carry.pop("pending_prereq_concept_id", None)

    # ── follow_up_suggestions ─────────────────────────────────────────────────
    suggestions = result.get("follow_up_suggestions")  # type: ignore[call-overload]
    if suggestions:
        carry["follow_up_suggestions"] = suggestions
    else:
        carry.pop("follow_up_suggestions", None)


def build_initial_state(
    user_id: str,
    session_id: str,
    query: str,
    carry: Dict[str, Any],
    graded_overrides: Optional[Dict[str, Any]] = None,
    roadmap_node_id: Optional[str] = None,
    roadmap_concept_id: Optional[str] = None,
    study_stage: Optional[str] = None,
    journey_type: Optional[str] = None,
    focus_chapters: Optional[List[str]] = None,
) -> TutorState:
    """Assemble initial state dictionary for tutor_graph.invoke."""
    state: TutorState = {
        "user_id": user_id,
        "session_id": session_id,
        "query": query,
        "roadmap_node_id": roadmap_node_id,
        "roadmap_concept_id": roadmap_concept_id,
        "study_stage": study_stage,
        "journey_type": journey_type,
        "focus_chapters": focus_chapters or [],
        # Graded defaults (may be overridden below)
        "has_graded_outcome": False,
        "student_answer": None,
        "is_correct": None,
        "error_type": None,
        "time_taken_sec": None,
        # Other defaults
        "trigger_quiz": False,
        "use_socratic": False,
        "socratic_turn_count": 0,
        "run_context_manager": False,
        "follow_up_suggestions": [],
        "quiz_steps": [],
        "secondary_concept_ids": [],
        "recent_turns": [],
        "prereq_concept_states": [],
        "retrieved_chunks": [],
        "pending_prereq_concept_id": None,
        "prior_session_summary": None,
        "awaiting_graded_response": False,
        "seen_follow_up_ids": [],
        "secondary_concept_states": [],
        "quiz_batch_mode": False,
        "quiz_batch_index": 0,
        "quiz_batch_chunks": [],
        "quiz_batch_size": 5,
        "mistake_brief": None,
        "remediation_flashcards": [],
        "score_summary": None,
        "sandbox_action": None,
        "sandbox_card": None,
        "problem_state": {
            "problem_key": "",
            "attempts_on_problem": 0,
            "hints_given": 0,
            "student_asked_for_answer": 0,
        },
    }
    # Merge carry-over fields from previous turn (skip UI-only blobs).
    for key, val in carry.items():
        if key in _CARRY_EXCLUDE_FROM_STATE:
            continue
        state[key] = val  # type: ignore[literal-required]

    # Request fields always win over stale carry
    state["user_id"] = user_id
    state["session_id"] = session_id
    state["query"] = query
    if roadmap_node_id is not None:
        state["roadmap_node_id"] = roadmap_node_id
    if roadmap_concept_id is not None:
        state["roadmap_concept_id"] = roadmap_concept_id
    if study_stage:
        state["study_stage"] = study_stage
        state["content_type_requested"] = normalize_content_type(study_stage)
    if journey_type is not None:
        state["journey_type"] = journey_type
    if focus_chapters is not None:
        state["focus_chapters"] = focus_chapters or []

    # Apply explicit graded overrides (highest priority)
    if graded_overrides:
        state.update(graded_overrides)  # type: ignore[typeddict-item]
    elif not study_stage:
        # Fresh message — do not inherit stale practice retrieval / content type.
        state["awaiting_graded_response"] = False
        state["retrieved_chunks"] = []
        state["prereq_concept_states"] = []
        state["secondary_concept_states"] = []
        state.pop("target_concept_state", None)
        state.pop("content_type_requested", None)
        state.pop("_prefetch_fast_forward", None)

    return state


def parse_legacy_answer(raw: str) -> Dict[str, Any]:
    """Parse legacy format: ANSWER::<answer>::<correct|incorrect>::<error_type>::<seconds>.

    NOTE: This format is deprecated. Active frontend clients submit structured
    button clicks or standard turn inputs. Retained for manual CLI debugging.
    """
    parts = raw[len("ANSWER::"):].split("::")
    student_answer = parts[0] if len(parts) > 0 else ""
    correctness = parts[1].lower() if len(parts) > 1 else "incorrect"
    error_type = parts[2].lower() if len(parts) > 2 else None
    try:
        time_taken = int(parts[3]) if len(parts) > 3 else None
    except ValueError:
        time_taken = None

    is_correct = correctness == "correct"
    return {
        "has_graded_outcome": True,
        "student_answer": student_answer,
        "is_correct": is_correct,
        "error_type": error_type if not is_correct else None,
        "time_taken_sec": time_taken,
    }


def parse_turn_input(
    raw_input: str,
    carry: Dict[str, Any],
    graded_outcome: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]], bool]:
    """Parse raw student turn input, detecting UI-level and text-level grading submissions.

    Returns:
        (query, graded_overrides, was_graded)
    """
    graded_overrides: Optional[Dict[str, Any]] = None
    query = raw_input
    was_graded = False

    outcome = (graded_outcome or "").lower().strip()
    # Explicit Correct/Incorrect from the UI is authoritative. Prefer it over the
    # raw query so clients never depend on sending "1"/"0" in `query`.
    if outcome in {"correct", "incorrect"}:
        awaiting = bool(carry.get("awaiting_graded_response"))
        ctype = normalize_content_type(carry.get("content_type_requested") or "")
        if awaiting or ctype in {"practice", "pyq"} or not raw_input.strip():
            graded_overrides = {
                "has_graded_outcome": True,
                "student_answer": "1" if outcome == "correct" else "0",
                "is_correct": outcome == "correct",
                "error_type": None,
                "time_taken_sec": None,
            }
            query = graded_overrides["student_answer"]
            was_graded = True
            return query, graded_overrides, was_graded

    stripped = raw_input.strip()
    if (
        stripped in {"1", "0"}
        and normalize_content_type(carry.get("content_type_requested") or "") in {"practice", "pyq"}
        and carry.get("awaiting_graded_response")
    ):
        is_correct = stripped == "1"
        graded_overrides = {
            "has_graded_outcome": True,
            "student_answer": stripped,
            "is_correct": is_correct,
            "error_type": None,
            "time_taken_sec": None,
        }
        was_graded = True
        return query, graded_overrides, was_graded

    if raw_input.upper().startswith("ANSWER::"):
        graded_overrides = parse_legacy_answer(raw_input)
        query = graded_overrides.get("student_answer", raw_input)
        was_graded = True

    return query, graded_overrides, was_graded


def resolve_session(
    user_id: str,
    session_id: Optional[str] = None,
    *,
    create_reason: str = "free",
    roadmap_node_id: Optional[str] = None,
) -> str:
    """Resolve an existing session or bootstrap a new one.

    Raises:
        KeyError: if session_id is provided but not found or user mismatch.
    """
    if session_id:
        session = db.read_session(session_id)
        if not session or session.get("user_id") != user_id:
            raise KeyError(f"Session not found or user mismatch: {session_id}")
        return session_id
    session = db.create_session(user_id)
    logger.warning(
        "session-create reason=%s user=%s session=%s roadmap_node=%s",
        create_reason,
        user_id,
        session["session_id"],
        roadmap_node_id or "-",
    )
    return session["session_id"]


def flush_session_summary(session_id: str, user_id: str) -> None:
    """Force a final Context Manager run on session exit."""
    try:
        flush_state: TutorState = {  # type: ignore[typeddict-item]
            "session_id": session_id,
            "user_id": user_id,
        }
        context_manager_node(flush_state)
    except Exception:
        logger.exception("Failed to flush session summary for session %s", session_id)
