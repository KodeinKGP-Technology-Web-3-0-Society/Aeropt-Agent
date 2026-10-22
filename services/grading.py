"""Shared grading contract and the one progress-write path.

A grade decision and a saved attempt are different results. Callers return both.
Progress is written only by ``commit_graded_attempt``. If that database function
is missing, nothing is written by a second, partial path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

import config
from db.supabase_client import db

logger = logging.getLogger("jee_tutor.grading")

GRADE_STATUSES = {
    "correct",
    "incorrect",
    "self_reported",
    "ungradable",
    "invalid_submission",
    "error",
}
EVIDENCE_SOURCES = {"answer_key", "student_self_report", "model_unverified", "none"}
PERSISTENCE_STATUSES = {"committed", "failed", "not_attempted", "unknown"}
_ERROR_TYPES = {"conceptual", "procedural", "calculation", "misread", "unknown"}


def stored_score(value: Any) -> Optional[float]:
    """A finite 0–1 score. Zero stays zero. Missing and non-finite stay missing."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > 1:
        return None
    return number


def measured_score(row: Optional[Dict[str, Any]]) -> Optional[float]:
    """The stored score only after a real attempt. A cold-start prior is unmeasured."""
    if not isinstance(row, dict):
        return None
    try:
        attempts = int(row.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts <= 0 or row.get("is_cold_start"):
        return None
    return stored_score(row.get("proficiency_score"))


def display_percent(score: Optional[float]) -> Optional[int]:
    if score is None:
        return None
    return max(0, min(100, int(round(score * 100))))


def update_proficiency(
    old_score: float,
    is_correct: bool,
    time_taken: Optional[int],
    avg_time: float,
    timed_attempts: int = 0,
) -> float:
    """new = alpha * old + (1 - alpha) * outcome.

    A slow correct multiplies the outcome by TIME_DAMPEN_FACTOR. Above 0.70
    that can reduce the stored score. That rule is intentional for this release.
    Dampening uses a measured timed average only. A missing, negative, or
    first-ever time does not invent a penalty.
    """
    outcome = 1.0 if is_correct else 0.0
    if (
        is_correct
        and timed_attempts > 0
        and avg_time > 0
        and time_taken is not None
        and time_taken >= 0
        and time_taken > config.TIME_DAMPEN_MULTIPLIER * avg_time
    ):
        outcome *= config.TIME_DAMPEN_FACTOR
    return config.PROFICIENCY_WMA_ALPHA * old_score + (1 - config.PROFICIENCY_WMA_ALPHA) * outcome


def valid_time(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def next_timing(old_avg: float, timed_attempts: int, time_taken: Optional[int]) -> tuple:
    """(new_avg, new_timed_count). Untimed and invalid samples do not move the average.

    A historical average whose sample count was never stored starts a new series
    on the first valid time. It is not divided by total attempts.
    """
    if time_taken is None:
        return old_avg, timed_attempts
    if timed_attempts <= 0:
        return float(time_taken), 1
    count = timed_attempts + 1
    return old_avg + (float(time_taken) - old_avg) / count, count


def answer_fingerprint(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class GradeResult:
    grade_status: str = "error"
    evidence_source: str = "none"
    persistence_status: str = "not_attempted"
    submission_id: Optional[str] = None
    attempt_id: Optional[str] = None
    question_instance_id: Optional[str] = None
    content_id: Optional[str] = None
    concept_id: Optional[str] = None
    is_correct: Optional[bool] = None
    proficiency_before: Optional[float] = None
    proficiency_after: Optional[float] = None
    proficiency_pct: Optional[int] = None
    failure_reason: Optional[str] = None
    answer_text: str = ""
    student_message: str = ""
    replayed: bool = False
    questions_served: Optional[int] = None
    questions_correct: Optional[int] = None
    difficulty_level: Optional[int] = None
    struggle_streak: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.grade_status not in GRADE_STATUSES:
            self.grade_status = "error"
        if self.evidence_source not in EVIDENCE_SOURCES:
            self.evidence_source = "none"
        if self.persistence_status not in PERSISTENCE_STATUSES:
            self.persistence_status = "unknown"
        if self.persistence_status != "committed":
            self.proficiency_before = None
            self.proficiency_after = None
            self.proficiency_pct = None

    @property
    def graded(self) -> bool:
        """Authoritative key produced correct or incorrect."""
        return self.grade_status in {"correct", "incorrect"} and self.evidence_source == "answer_key"

    @property
    def recorded(self) -> bool:
        return self.persistence_status == "committed" and bool(self.attempt_id)

    def to_public(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload.pop("extra", None)
        payload.update(self.extra)
        payload["graded"] = self.graded
        payload["recorded"] = self.recorded
        payload["correct"] = self.is_correct if self.graded else None
        if self.grade_status == "self_reported":
            payload["self_report_correct"] = self.is_correct
        if self.persistence_status != "committed":
            payload["proficiency_before"] = None
            payload["proficiency_after"] = None
            payload["proficiency_pct"] = None
        return {key: value for key, value in payload.items() if value is not None or key in {
            "is_correct", "correct", "graded", "recorded", "proficiency_pct",
        }}


def student_message(result: GradeResult, *, cold_start: bool = False) -> str:
    """One verdict. Score text appears only after a commit."""
    if result.persistence_status == "unknown":
        return (
            "The save did not confirm. Send the same submission again and it will "
            "be recovered, not counted a second time."
        )
    if result.failure_reason == "submission_conflict":
        return "That submission id was already used for a different answer. Your progress was not changed."
    if result.failure_reason == "persistence_unavailable":
        checked = _checked_clause(result)
        return f"{checked} Progress could not be saved because grading storage is not available."
    if result.failure_reason == "no_pending_question":
        return "No open question matched that check, so your progress was not changed."
    if result.grade_status == "invalid_submission":
        return "That submission could not be read as an answer, so your progress was not changed."
    if result.grade_status == "ungradable":
        return "This answer could not be checked against a key, so your progress was not changed."
    if result.evidence_source == "model_unverified":
        return "There is no stored answer key for this question, so it was not saved as a grade."
    if result.grade_status == "self_reported":
        mark = "correct" if result.is_correct else "incorrect"
        base = f"You marked this {mark}. That is your own check, not an answer-key check."
    elif result.grade_status == "correct":
        base = "Correct."
        if result.answer_text:
            base = f"Correct. The answer is {result.answer_text}."
    elif result.grade_status == "incorrect":
        base = "Incorrect."
        if result.answer_text:
            base = f"Incorrect. The answer is {result.answer_text}."
    else:
        base = "This attempt could not be graded."
    if result.persistence_status == "committed" and result.proficiency_pct is not None:
        if cold_start:
            return f"{base} Your proficiency is now {result.proficiency_pct}%."
        before = display_percent(result.proficiency_before)
        if before is None:
            return f"{base} Your proficiency is now {result.proficiency_pct}%."
        return f"{base} Your proficiency went from {before}% to {result.proficiency_pct}%."
    if result.graded or result.grade_status == "self_reported":
        return f"{base} Your progress was not saved."
    return base


def _checked_clause(result: GradeResult) -> str:
    if result.grade_status == "correct":
        return "Your answer was checked: correct."
    if result.grade_status == "incorrect":
        return "Your answer was checked: incorrect."
    if result.grade_status == "self_reported":
        mark = "correct" if result.is_correct else "incorrect"
        return f"You marked this {mark}."
    return "The answer was not given a verified grade."


def _result_from_decision(decision: Dict[str, Any], **identity: Any) -> GradeResult:
    status = decision.get("grade_status") or "ungradable"
    evidence = decision.get("evidence_source") or "none"
    is_correct = decision.get("is_correct") if status in {"correct", "incorrect"} else None
    result = GradeResult(
        grade_status=status,
        evidence_source=evidence,
        persistence_status="not_attempted",
        is_correct=is_correct,
        answer_text=decision.get("answer_text") or "",
        failure_reason=decision.get("failure_reason"),
        **identity,
    )
    result.student_message = student_message(result)
    return result


def _safe_reason(status: str, reason: Optional[str]) -> str:
    allowed = {
        "rejected": "progress_rejected",
        "conflict": "submission_conflict",
        "replay": None,
        "committed": None,
    }
    if status in allowed and allowed[status]:
        return allowed[status] or ""
    if reason in {
        "submission_conflict", "no_concept_state", "session_not_owned",
        "study_session_not_owned", "invalid_user", "invalid_proficiency",
        "persistence_unavailable", "progress_write_failed", "no_pending_question",
        "identity_unavailable", "cancelled",
    }:
        return reason
    return "progress_write_failed"


def _missing_migration(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "pgrst202" in text
        or "could not find the function" in text
        or ("commit_graded_attempt" in text and ("does not exist" in text or "schema cache" in text))
    )


def _uncertain(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text or "statement timeout" in text


def commit_progress(
    *,
    user_id: str,
    session_id: str,
    concept_id: str,
    content_id: Optional[str],
    submission_id: Optional[str],
    question_instance_id: Optional[str],
    student_answer: str,
    fingerprint: str,
    is_correct: Optional[bool],
    grade_status: str,
    evidence_source: str,
    error_type: Optional[str] = None,
    time_taken_sec: Optional[int] = None,
    source: str = "chat",
    study_session_id: Optional[str] = None,
    question_difficulty: Optional[int] = None,
    answer_text: str = "",
    cold_start: bool = False,
) -> GradeResult:
    """Insert one attempt and its proficiency change, or return the original row."""
    identity = dict(
        submission_id=submission_id,
        question_instance_id=question_instance_id,
        content_id=content_id,
        concept_id=concept_id,
        answer_text=answer_text,
    )
    writable = (
        (evidence_source == "answer_key" and grade_status in {"correct", "incorrect"} and isinstance(is_correct, bool))
        or (evidence_source == "student_self_report" and grade_status == "self_reported" and isinstance(is_correct, bool))
    )
    if not writable:
        result = GradeResult(
            grade_status=grade_status if grade_status in GRADE_STATUSES else "ungradable",
            evidence_source=evidence_source if evidence_source in EVIDENCE_SOURCES else "none",
            persistence_status="not_attempted",
            is_correct=is_correct if grade_status in {"correct", "incorrect", "self_reported"} else None,
            failure_reason=None,
            **identity,
        )
        result.student_message = student_message(result)
        return result
    if not submission_id or not concept_id or not user_id:
        result = GradeResult(
            grade_status=grade_status,
            evidence_source=evidence_source,
            persistence_status="not_attempted",
            is_correct=is_correct,
            failure_reason="identity_unavailable",
            **identity,
        )
        result.student_message = student_message(result)
        return result
    from services import progress as turn_progress

    if turn_progress.is_cancelled():
        result = GradeResult(
            grade_status=grade_status,
            evidence_source=evidence_source,
            persistence_status="not_attempted",
            is_correct=is_correct,
            failure_reason="cancelled",
            **identity,
        )
        result.student_message = student_message(result, cold_start=cold_start)
        return result

    stored_error = error_type if error_type in _ERROR_TYPES else None
    if is_correct:
        stored_error = None
    elif stored_error is None:
        stored_error = "unknown"
    request = {
        "user_id": user_id,
        "session_id": session_id or None,
        "concept_id": concept_id,
        "content_id": content_id,
        "submission_id": submission_id,
        "question_instance_id": question_instance_id,
        "answer_fingerprint": fingerprint,
        "student_answer": (student_answer or "")[:2000],
        "is_correct": bool(is_correct),
        "grade_status": grade_status,
        "evidence_source": evidence_source,
        "error_type": stored_error,
        "time_taken_sec": valid_time(time_taken_sec),
        "source": source,
        "study_session_id": study_session_id,
        "question_difficulty": question_difficulty,
        "answer_text": answer_text,
        "alpha": config.PROFICIENCY_WMA_ALPHA,
        "dampen_multiplier": config.TIME_DAMPEN_MULTIPLIER,
        "dampen_factor": config.TIME_DAMPEN_FACTOR,
        "weak_threshold": config.WEAK_THRESHOLD,
        "clear_weak_threshold": config.CLEAR_WEAK_THRESHOLD,
        "difficulty_up_streak": config.STUDY_DIFFICULTY_UP_STREAK,
        "difficulty_down_streak": config.STUDY_DIFFICULTY_DOWN_STREAK,
        "difficulty_min": config.STUDY_DIFFICULTY_MIN,
        "difficulty_max": config.STUDY_DIFFICULTY_MAX,
    }
    try:
        row = db.commit_graded_attempt(request)
    except Exception as exc:
        logger.warning("graded attempt commit failed", exc_info=True)
        reason = "persistence_unavailable" if _missing_migration(exc) else (
            "progress_write_uncertain" if _uncertain(exc) else "progress_write_failed"
        )
        status = "unknown" if reason == "progress_write_uncertain" else "failed"
        result = GradeResult(
            grade_status=grade_status,
            evidence_source=evidence_source,
            persistence_status=status,
            is_correct=is_correct,
            failure_reason=reason if reason != "progress_write_uncertain" else None,
            **identity,
        )
        if status == "unknown":
            result.failure_reason = "progress_write_uncertain"
        result.student_message = student_message(result, cold_start=cold_start)
        return result

    if not isinstance(row, dict):
        result = GradeResult(
            grade_status=grade_status,
            evidence_source=evidence_source,
            persistence_status="failed",
            is_correct=is_correct,
            failure_reason="progress_write_failed",
            **identity,
        )
        result.student_message = student_message(result)
        return result
    rpc_status = str(row.get("status") or "")
    if rpc_status in {"committed", "replay"}:
        before = stored_score(row.get("proficiency_before"))
        after = stored_score(row.get("proficiency_after"))
        result = GradeResult(
            grade_status=str(row.get("grade_status") or grade_status),
            evidence_source=str(row.get("evidence_source") or evidence_source),
            persistence_status="committed",
            submission_id=row.get("submission_id") or submission_id,
            attempt_id=str(row["attempt_id"]) if row.get("attempt_id") else None,
            question_instance_id=row.get("question_instance_id") or question_instance_id,
            content_id=row.get("content_id") or content_id,
            concept_id=row.get("concept_id") or concept_id,
            is_correct=row.get("is_correct") if isinstance(row.get("is_correct"), bool) else is_correct,
            proficiency_before=before,
            proficiency_after=after,
            proficiency_pct=display_percent(after),
            answer_text=row.get("answer_text") or answer_text,
            replayed=rpc_status == "replay",
            questions_served=row.get("questions_served"),
            questions_correct=row.get("questions_correct"),
            difficulty_level=row.get("difficulty_level"),
            struggle_streak=row.get("struggle_streak") if isinstance(row.get("struggle_streak"), int) else None,
        )
        result.student_message = student_message(result, cold_start=cold_start or bool(row.get("was_cold_start")))
        return result
    result = GradeResult(
        grade_status=grade_status,
        evidence_source=evidence_source,
        persistence_status="failed",
        is_correct=is_correct,
        failure_reason=_safe_reason(rpc_status, row.get("failure_reason")),
        **identity,
    )
    result.student_message = student_message(result, cold_start=cold_start)
    return result
