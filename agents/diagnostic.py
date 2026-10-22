"""Record one explicit self-report against the question that was actually served.

Proficiency, the attempt, and the learning ledger are written together by
``commit_graded_attempt``. This module does not classify a missing error as
conceptual, does not invent a prerequisite cause, and does not insert a chat row.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import config
from models.state import TutorState
from services.grading import answer_fingerprint, commit_progress, update_proficiency

_VALID_ERROR_TYPES = {"conceptual", "procedural", "calculation", "misread"}


def _update_proficiency(
    old_score: float,
    is_correct: bool,
    time_taken: Optional[int],
    avg_time: float,
) -> float:
    """Retained formula. A positive average counts as a measured timed baseline."""
    return update_proficiency(
        old_score,
        is_correct,
        time_taken,
        avg_time,
        timed_attempts=1 if avg_time and avg_time > 0 else 0,
    )


def diagnostic_node(state: TutorState) -> Dict[str, Any]:
    if not state.get("has_graded_outcome"):
        return {"trigger_quiz": False, "assessment_status": "not_requested"}
    if state.get("evidence_source") != "student_self_report":
        return {
            "error": None,
            "assessment_status": "not_requested",
            "attempt_record_failed": True,
            "persistence_status": "not_attempted",
            "grade_status": "error",
            "evidence_source": "none",
            "failure_reason": "self_report_route_required",
            "trigger_quiz": False,
            "response": "That check was not a confirmed answer, so your progress was not changed.",
        }
    if not isinstance(state.get("is_correct"), bool):
        return {
            "error": "assessment_missing_correctness",
            "assessment_status": "failed",
            "trigger_quiz": False,
        }

    is_correct = bool(state["is_correct"])
    question_instance_id = state.get("question_instance_id") or ""
    content_id = state.get("graded_content_id") or ""
    concept_id = state.get("primary_concept_id") or ""
    if not question_instance_id or not content_id or not concept_id:
        return {
            "error": None,
            "attempt_record_failed": True,
            "persistence_status": "not_attempted",
            "grade_status": "error",
            "evidence_source": "student_self_report",
            "failure_reason": "no_pending_question",
            "trigger_quiz": False,
            "response": "No open question matched that check, so your progress was not changed.",
        }

    supplied_error = state.get("error_type")
    error_type = supplied_error if supplied_error in _VALID_ERROR_TYPES else None
    student_answer = state.get("student_answer") or ("1" if is_correct else "0")
    submission_id = state.get("submission_id") or question_instance_id
    target = state.get("target_concept_state") or {}
    recorded = commit_progress(
        user_id=state["user_id"],
        session_id=state.get("session_id") or "",
        concept_id=concept_id,
        content_id=content_id,
        submission_id=submission_id,
        question_instance_id=question_instance_id,
        student_answer=str(student_answer),
        fingerprint=answer_fingerprint({
            "student_answer": str(student_answer),
            "self_report": is_correct,
            "content_id": content_id,
            "question_instance_id": question_instance_id,
        }),
        is_correct=is_correct,
        grade_status="self_reported",
        evidence_source="student_self_report",
        error_type=error_type,
        time_taken_sec=state.get("time_taken_sec"),
        source=state.get("attempt_source") or "practice",
        study_session_id=state.get("study_session_id"),
        cold_start=bool(target.get("is_cold_start")) or int(target.get("attempts") or 0) == 0,
    )
    public = recorded.to_public()
    return {
        "error": None,
        "trigger_quiz": bool(
            recorded.recorded
            and not is_correct
            and (recorded.struggle_streak or 0) >= config.STRUGGLE_STREAK_QUIZ_TRIGGER
        ),
        "response": recorded.student_message,
        "agent_used": "graded_fast_path",
        "attempt_recorded": recorded.recorded,
        "attempt_record_failed": not recorded.recorded,
        "attempt_id": recorded.attempt_id,
        "proficiency_before": recorded.proficiency_before,
        "proficiency_after": recorded.proficiency_after,
        "mastery_pct": recorded.proficiency_pct,
        "grade_status": recorded.grade_status,
        "evidence_source": recorded.evidence_source,
        "persistence_status": recorded.persistence_status,
        "failure_reason": recorded.failure_reason,
        "recorded_concept_id": concept_id,
        "assessment_status": "completed" if recorded.recorded else "persistence_failed",
        "score_summary": recorded.student_message if recorded.recorded and not is_correct else None,
        "is_correct": is_correct,
        "graded": public["graded"],
        "recorded": public["recorded"],
    }
