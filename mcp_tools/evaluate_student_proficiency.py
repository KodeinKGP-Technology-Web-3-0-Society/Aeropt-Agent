"""
MCP Tool: Evaluate Student Proficiency
Encapsulates the Diagnostic Agent logic as a standalone tool.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone

import config
from db.supabase_client import db
from utils.llm import groq_complete
from openai import OpenAI

_groq = OpenAI(api_key=config.GEMINI_API_KEYS[0], base_url="https://generativelanguage.googleapis.com/v1beta/openai/")


class EvaluateProficiencyArgs(BaseModel):
    user_id: str = Field(..., description="The user's UUID.")
    session_id: str = Field(..., description="The current chat session UUID.")
    concept_id: str = Field(..., description="The primary concept ID being evaluated.")
    content_id: Optional[str] = Field(default=None, description="The specific question/content ID being answered.")
    is_correct: bool = Field(..., description="Whether the student's answer was correct.")
    time_taken_sec: Optional[int] = Field(default=None, description="Time taken in seconds, if known.")
    student_answer: Optional[str] = Field(default=None, description="The student's raw answer text.")
    question: Optional[str] = Field(default=None, description="The text of the question asked.")
    correct_answer: Optional[str] = Field(default=None, description="The correct answer or solution steps.")
    error_type: Optional[str] = Field(default=None, description="Pre-classified error type, if any.")


_ERROR_CLASSIFY_PROMPT = """\
Classify the student's error when solving a JEE Physics problem.
Pick exactly ONE error type:

  conceptual  – wrong understanding of the physical concept
  procedural  – correct concept but wrong method / steps
  calculation – correct method but arithmetic / algebra mistake
  misread     – misread the question (wrong numbers, missing units, etc.)

Question:        {question}
Correct answer:  {correct_answer}
Student answer:  {student_answer}

Reply with ONLY the error type word."""

_VALID_ERROR_TYPES = {"conceptual", "procedural", "calculation", "misread"}


def _classify_error_type(question: str, student_answer: str, correct_answer: str) -> str:
    prompt = _ERROR_CLASSIFY_PROMPT.format(
        question=(question or "")[:500],
        correct_answer=(correct_answer or "")[:200],
        student_answer=(student_answer or "")[:200],
    )
    try:
        raw, _ = groq_complete(
            client=_groq,
            model=config.DIAGNOSTIC_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.0,
            max_tokens=10,
        )
        raw = raw.strip().lower()
        for et in _VALID_ERROR_TYPES:
            if et in raw:
                return et
    except Exception:
        pass
    return "conceptual"


def _incremental_avg(old_avg: float, new_value: float, new_n: int) -> float:
    if new_n <= 0:
        return new_value
    return old_avg + (new_value - old_avg) / new_n


def apply_proficiency_decay(score: float, last_attempted_at: Optional[str]) -> tuple:
    if not last_attempted_at or score <= 0:
        return score, False

    try:
        if isinstance(last_attempted_at, str):
            last_dt = datetime.fromisoformat(last_attempted_at.replace("Z", "+00:00"))
        else:
            last_dt = last_attempted_at
        now = datetime.now(timezone.utc)
        days_since = (now - last_dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return score, False

    if days_since < 7:
        return score, False

    decay_factor = 0.5 ** (days_since / 30)
    decayed = score * decay_factor
    floor = score * 0.6
    decayed = max(decayed, floor)

    return round(decayed, 4), True


def _update_proficiency(old_score: float, is_correct: bool, time_taken: Optional[int], avg_time: float) -> float:
    outcome = 1.0 if is_correct else 0.0
    if is_correct and avg_time > 0 and time_taken is not None and time_taken > config.TIME_DAMPEN_MULTIPLIER * avg_time:
        outcome *= config.TIME_DAMPEN_FACTOR
    return config.PROFICIENCY_WMA_ALPHA * old_score + (1 - config.PROFICIENCY_WMA_ALPHA) * outcome


def _check_consecutive_correct(user_id: str, concept_id: str) -> int:
    attempts = db.read_recent_user_attempts(user_id, concept_id, n=5)
    count = 0
    for a in attempts:
        if a.get("is_correct"):
            count += 1
        else:
            break
    return count


def evaluate_student_proficiency(args: EvaluateProficiencyArgs) -> Dict[str, Any]:
    """
    Grades a student's answer, classifies the error type if wrong, and updates their 
    long-term proficiency state in the database.
    """
    try:
        error_type = args.error_type
        if not args.is_correct and not error_type:
            error_type = _classify_error_type(
                question=args.question or "",
                student_answer=args.student_answer or "",
                correct_answer=args.correct_answer or "",
            )

        cs = db.read_user_concept_state(args.user_id, args.concept_id)
        if not cs:
            return {"error": f"No concept state for {args.user_id}/{args.concept_id}"}

        old_score = cs.get("proficiency_score", 0.5)
        old_avg_time = cs.get("avg_time_seconds", 0.0)
        old_attempts = cs.get("attempts", 0)
        old_correct = cs.get("correct", 0)
        old_streak = cs.get("struggle_streak", 0)
        old_flagged = cs.get("flagged_weak", False)
        old_consec_correct = cs.get("consecutive_correct", 0)
        old_error_dist: Dict[str, int] = cs.get("error_type_dist") or {
            "conceptual": 0,
            "procedural": 0,
            "calculation": 0,
            "misread": 0,
        }

        last_attempted = cs.get("last_attempted_at")
        decayed_score, was_decayed = apply_proficiency_decay(old_score, last_attempted)
        if was_decayed:
            old_score = decayed_score

        new_attempts = old_attempts + 1
        new_correct = old_correct + (1 if args.is_correct else 0)
        new_consec_correct = (old_consec_correct + 1) if args.is_correct else 0

        new_score = _update_proficiency(old_score, args.is_correct, args.time_taken_sec, old_avg_time)
        new_streak = 0 if args.is_correct else old_streak + 1

        new_avg_time = (
            _incremental_avg(old_avg_time, float(args.time_taken_sec), new_attempts)
            if args.time_taken_sec is not None
            else old_avg_time
        )

        new_error_dist = dict(old_error_dist)
        if not args.is_correct and error_type in new_error_dist:
            new_error_dist[error_type] += 1

        if new_score < config.WEAK_THRESHOLD:
            new_flagged = True
        elif new_score > config.CLEAR_WEAK_THRESHOLD:
            consec = _check_consecutive_correct(args.user_id, args.concept_id)
            new_flagged = False if (consec + (1 if args.is_correct else 0)) >= 2 else old_flagged
        else:
            new_flagged = old_flagged

        now_iso = datetime.now(timezone.utc).isoformat()
        db.update_user_concept_state(
            args.user_id,
            args.concept_id,
            {
                "proficiency_score": round(new_score, 4),
                "attempts": new_attempts,
                "correct": new_correct,
                "avg_time_seconds": round(new_avg_time, 2),
                "struggle_streak": new_streak,
                "flagged_weak": new_flagged,
                "consecutive_correct": new_consec_correct,
                "error_type_dist": new_error_dist,
                "last_attempted_at": now_iso,
                "is_cold_start": False,
                "decay_applied": False,
            },
        )

        db.insert_user_attempt(
            user_id=args.user_id,
            session_id=args.session_id,
            concept_id=args.concept_id,
            content_id=args.content_id,
            is_correct=args.is_correct,
            time_taken_sec=args.time_taken_sec,
            error_type=error_type if not args.is_correct else None,
        )
        
        trigger_quiz = new_streak >= config.STRUGGLE_STREAK_QUIZ_TRIGGER

        return {
            "success": True,
            "new_proficiency_score": new_score,
            "trigger_quiz": trigger_quiz,
            "error_type": error_type if not args.is_correct else None,
            "flagged_weak": new_flagged
        }

    except Exception as exc:
        import logging
        logging.exception("evaluate_student_proficiency tool failed.")
        return {"success": False, "error": str(exc)}
