"""Shared turn, session, carry, and grading services for Aeropt agent entry points.

Used by both CLI (`main.py`) and HTTP API (`api_server.py`) to eliminate logic drift.
"""

from __future__ import annotations

import logging
import uuid
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


def persist_assistant_reply(session_id: str, result: TutorState) -> None:
    """Save the tutor reply so the next turn can see the question it asked.

    Bootstrap already stores the student message. Graded paths store their own
    assistant row; this writes the reply only when that exact text is not there.
    A failed write leaves the printed turn intact.
    """
    response = str(result.get("response") or "").strip()
    interaction = result.get("interaction_count")
    if not session_id or not response or not interaction:
        return
    turn_index = int(interaction) * 2
    try:
        existing = db.assistant_turn_contents(session_id, turn_index)
        if response in existing:
            return
        primary = result.get("primary_concept_id")
        db.insert_chat_turn(
            session_id=session_id,
            role="assistant",
            content=response,
            turn_index=turn_index,
            agent_used=result.get("agent_used") or "learning_agent",
            concepts_referenced=[primary] if primary else None,
        )
    except Exception:
        logger.warning(
            "could not store assistant reply session=%s turn_index=%s",
            session_id,
            turn_index,
            exc_info=True,
        )


def _served_chunk(chunks: Any) -> Optional[Dict[str, Any]]:
    """The one question this turn put in front of the student, if that is unambiguous."""
    if not isinstance(chunks, list):
        return None
    rows = [row for row in chunks if isinstance(row, dict)]
    practice = [
        row for row in rows
        if normalize_content_type(str(row.get("content_type") or "")) in {"practice", "pyq"}
    ]
    if len(practice) == 1:
        return practice[0]
    if len(rows) == 1:
        return rows[0]
    return None


def _sync_pending_question(carry: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Mint one stable question instance when a question starts waiting for an answer."""
    if not carry.get("awaiting_graded_response"):
        return
    chunk = _served_chunk(carry.get("retrieved_chunks"))
    if not chunk or not chunk.get("content_id"):
        return
    pending = carry.get("pending_question") if isinstance(carry.get("pending_question"), dict) else {}
    if pending.get("content_id") == chunk.get("content_id") and pending.get("question_instance_id"):
        return
    carry["pending_question"] = {
        "question_instance_id": str(uuid.uuid4()),
        "content_id": chunk.get("content_id"),
        "concept_id": result.get("primary_concept_id") or carry.get("primary_concept_id"),
    }


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
    persisted = result.get("persistence_status")  # type: ignore[call-overload]
    if new_awaiting is True:
        carry["awaiting_graded_response"] = True
    elif new_awaiting is False or (was_graded and persisted in {None, "committed"}):
        carry.pop("awaiting_graded_response", None)
        if persisted == "committed":
            carry.pop("pending_question", None)

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
        _sync_pending_question(carry, result)
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
            _sync_pending_question(carry, result)
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
    # A Correct/Incorrect button is a self-report for the pending served question.
    # An empty message or a stale click is not enough.
    pending = carry.get("pending_question") if isinstance(carry.get("pending_question"), dict) else {}
    if outcome in {"correct", "incorrect"}:
        if (
            carry.get("awaiting_graded_response")
            and pending.get("question_instance_id")
            and pending.get("content_id")
        ):
            graded_overrides = {
                "has_graded_outcome": True,
                "evidence_source": "student_self_report",
                "student_answer": "1" if outcome == "correct" else "0",
                "is_correct": outcome == "correct",
                "error_type": None,
                "time_taken_sec": None,
                "question_instance_id": pending["question_instance_id"],
                "graded_content_id": pending["content_id"],
                "submission_id": pending["question_instance_id"],
            }
            if pending.get("concept_id"):
                graded_overrides["primary_concept_id"] = pending["concept_id"]
            query = graded_overrides["student_answer"]
            was_graded = True
            return query, graded_overrides, was_graded

    stripped = raw_input.strip()
    if (
        stripped in {"1", "0"}
        and normalize_content_type(carry.get("content_type_requested") or "") in {"practice", "pyq"}
        and carry.get("awaiting_graded_response")
        and pending.get("question_instance_id")
        and pending.get("content_id")
    ):
        is_correct = stripped == "1"
        graded_overrides = {
            "has_graded_outcome": True,
            "evidence_source": "student_self_report",
            "student_answer": stripped,
            "is_correct": is_correct,
            "error_type": None,
            "time_taken_sec": None,
            "question_instance_id": pending["question_instance_id"],
            "graded_content_id": pending["content_id"],
            "submission_id": pending["question_instance_id"],
        }
        if pending.get("concept_id"):
            graded_overrides["primary_concept_id"] = pending["concept_id"]
        was_graded = True
        return query, graded_overrides, was_graded

    if raw_input.upper().startswith("ANSWER::") and pending.get("question_instance_id") and pending.get("content_id"):
        graded_overrides = parse_legacy_answer(raw_input)
        graded_overrides["evidence_source"] = "student_self_report"
        graded_overrides["question_instance_id"] = pending["question_instance_id"]
        graded_overrides["graded_content_id"] = pending["content_id"]
        graded_overrides["submission_id"] = pending["question_instance_id"]
        if pending.get("concept_id"):
            graded_overrides["primary_concept_id"] = pending["concept_id"]
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
