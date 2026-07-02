"""
Diagnostic Agent  (Section 9)

Fires ASYNCHRONOUSLY after any interaction with a graded outcome:
  - student answered a quiz step
  - student answered a practice question
  - student answered a PYQ

Does NOT fire after a pure explanation-only doubt (no answer was submitted).

Responsibilities:
  1. Classify error_type if not already supplied (llama-3.1-8b-instant)
  2. Update proficiency_score via weighted moving average (α=0.7/0.3)
  3. Update struggle_streak (increment on wrong, reset to 0 on first correct)
  4. Update flagged_weak (set < 0.4, clear > 0.65 + 2 consecutive correct)
  5. Update error_type_dist tally
  6. Update avg_time_seconds (incremental rolling average)
  7. Clear is_cold_start on first graded attempt regardless of correctness
  8. Write a new user_attempts row
  9. Return trigger_quiz = True if struggle_streak >= STRUGGLE_STREAK_QUIZ_TRIGGER

Model: llama-3.1-8b-instant (Groq) — only needed for error classification
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from groq import Groq

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import groq_complete

_groq = Groq(api_key=config.GROQ_API_KEY)

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


def _classify_error_type(
    question: str, student_answer: str, correct_answer: str
) -> str:
    prompt = _ERROR_CLASSIFY_PROMPT.format(
        question=question[:500],
        correct_answer=correct_answer[:200],
        student_answer=student_answer[:200],
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
    return "conceptual"  # safe default


def _incremental_avg(old_avg: float, new_value: float, new_n: int) -> float:
    """Welford's online mean update: O(1), no history needed."""
    if new_n <= 0:
        return new_value
    return old_avg + (new_value - old_avg) / new_n


def _update_proficiency(
    old_score: float,
    is_correct: bool,
    time_taken: Optional[int],
    avg_time: float,
) -> float:
    outcome = 1.0 if is_correct else 0.0

    # Time dampening for slow-but-correct answers (Section 9)
    if (
        is_correct
        and avg_time > 0
        and time_taken is not None
        and time_taken > config.TIME_DAMPEN_MULTIPLIER * avg_time
    ):
        outcome *= config.TIME_DAMPEN_FACTOR

    return config.PROFICIENCY_WMA_ALPHA * old_score + (1 - config.PROFICIENCY_WMA_ALPHA) * outcome


def _check_consecutive_correct(user_id: str, concept_id: str) -> int:
    """Return count of consecutive correct attempts from most-recent backwards."""
    attempts = db.read_recent_user_attempts(user_id, concept_id, n=5)
    count = 0
    for a in attempts:
        if a.get("is_correct"):
            count += 1
        else:
            break
    return count


def diagnostic_node(state: TutorState) -> Dict[str, Any]:
    if not state.get("has_graded_outcome"):
        # Nothing to grade — skip silently
        return {"trigger_quiz": False}

    user_id = state["user_id"]
    session_id = state["session_id"]
    primary_id = state.get("primary_concept_id", "")
    is_correct: bool = state.get("is_correct", False)
    error_type: Optional[str] = state.get("error_type")
    time_taken: Optional[int] = state.get("time_taken_sec")
    student_answer: Optional[str] = state.get("student_answer")

    # Content_id from the retrieved chunk that was being answered
    chunks = state.get("retrieved_chunks", [])
    content_id: Optional[str] = chunks[0]["content_id"] if chunks else None

    try:
        # ── 1. Classify error if not already provided ─────────────────────────
        if not is_correct and not error_type:
            correct_answer = chunks[0].get("solution", "") if chunks else ""
            error_type = _classify_error_type(
                question=state["query"],
                student_answer=student_answer or "",
                correct_answer=correct_answer,
            )

        # ── 2. Load current concept state ────────────────────────────────────
        cs = db.read_user_concept_state(user_id, primary_id)
        if not cs:
            return {"error": f"No concept state for {user_id}/{primary_id}"}

        old_score = cs.get("proficiency_score", 0.5)
        old_avg_time = cs.get("avg_time_seconds", 0.0)
        old_attempts = cs.get("attempts", 0)
        old_correct = cs.get("correct", 0)
        old_streak = cs.get("struggle_streak", 0)
        old_flagged = cs.get("flagged_weak", False)
        old_error_dist: Dict[str, int] = cs.get("error_type_dist") or {
            "conceptual": 0,
            "procedural": 0,
            "calculation": 0,
            "misread": 0,
        }

        # ── 3. Compute new values ─────────────────────────────────────────────
        new_attempts = old_attempts + 1
        new_correct = old_correct + (1 if is_correct else 0)

        new_score = _update_proficiency(old_score, is_correct, time_taken, old_avg_time)

        new_streak = 0 if is_correct else old_streak + 1

        # avg_time
        new_avg_time = (
            _incremental_avg(old_avg_time, float(time_taken), new_attempts)
            if time_taken is not None
            else old_avg_time
        )

        # error_type_dist
        new_error_dist = dict(old_error_dist)
        if not is_correct and error_type in new_error_dist:
            new_error_dist[error_type] += 1

        # flagged_weak: set if below WEAK_THRESHOLD
        if new_score < config.WEAK_THRESHOLD:
            new_flagged = True
        elif new_score > config.CLEAR_WEAK_THRESHOLD:
            # Clear only if 2 consecutive correct (including this one)
            consec = _check_consecutive_correct(user_id, primary_id)
            new_flagged = False if (consec + (1 if is_correct else 0)) >= 2 else old_flagged
        else:
            new_flagged = old_flagged

        # ── 4. Write updated concept state ────────────────────────────────────
        db.update_user_concept_state(
            user_id,
            primary_id,
            {
                "proficiency_score": round(new_score, 4),
                "attempts": new_attempts,
                "correct": new_correct,
                "struggle_streak": new_streak,
                "flagged_weak": new_flagged,
                "is_cold_start": False,  # always clear on first graded attempt
                "avg_time_seconds": round(new_avg_time, 2),
                "error_type_dist": new_error_dist,
            },
        )

        # ── 5. Insert user_attempt row ────────────────────────────────────────
        db.insert_user_attempt(
            user_id=user_id,
            session_id=session_id,
            concept_id=primary_id,
            content_id=content_id,
            is_correct=is_correct,
            error_type=error_type if not is_correct else None,
            time_taken_sec=time_taken,
            student_answer=student_answer,
        )

        # ── 6. Write outcome summary to chat_turns ────────────────────────────
        # The Context Manager reads chat_turns to build session summaries.
        # Without this record it cannot tell whether the student answered
        # correctly or not and produces inaccurate summaries (Bug 4).
        outcome_parts = [
            f"[Diagnostic] Student answered "
            f"{'correctly ✓' if is_correct else 'incorrectly ✗'} on concept '{primary_id}'.",
            f"Proficiency: {round(old_score, 2)} → {round(new_score, 2)}.",
        ]
        if not is_correct and error_type:
            outcome_parts.append(f"Error type: {error_type}.")
        outcome_summary = " ".join(outcome_parts)

        db.insert_chat_turn(
            session_id=session_id,
            role="assistant",
            content=outcome_summary,
            turn_index=state.get("interaction_count", 1) * 2,
            agent_used="diagnostic",
            concepts_referenced=[primary_id] if primary_id else None,
        )

        # ── 7. Check quiz trigger ─────────────────────────────────────────────
        trigger_quiz = new_streak >= config.STRUGGLE_STREAK_QUIZ_TRIGGER

        # ── 8. Score summary message for incorrect answers ────────────────────
        # Only when the student was wrong — the Follow-Up "Well done!" covers
        # the correct path.  All data comes from state; no new Supabase reads.
        result: Dict[str, Any] = {"trigger_quiz": trigger_quiz, "error": None}

        if not is_correct:
            prereq_states     = state.get("prereq_concept_states") or []
            secondary_states  = state.get("secondary_concept_states") or []

            # ── Primary concept line ──────────────────────────────────────────
            primary_name = primary_id.replace("_", " ").title()
            primary_line = (
                f"- **{primary_name}** (primary): "
                f"{round(old_score, 2)} → **{round(new_score, 2)}**"
            )
            if error_type:
                primary_line += f"  *(error: {error_type})*"

            lines: List[str] = ["**Here is where you stand after this attempt:**\n", primary_line]

            # ── Prerequisite concepts ─────────────────────────────────────────
            if prereq_states:
                lines.append("\n**Prerequisites:**")
                for p in prereq_states[:5]:
                    name = p.get("concept_name") or p.get("concept_id", "unknown")
                    if p.get("is_cold_start"):
                        lines.append(f"  - **{name}**: not yet studied")
                    else:
                        score = round(p.get("proficiency_score", 0.0), 2)
                        weak  = " ⚠️ gap" if (p.get("flagged_weak") or score < config.WEAK_THRESHOLD) else ""
                        lines.append(f"  - **{name}**: {score}{weak}")
                    if p.get("is_cold_start"):
                        lines.append(f"    *(covering this first may help)*")

            # ── Secondary concepts ────────────────────────────────────────────
            if secondary_states:
                lines.append("\n**Related concepts:**")
                for s in secondary_states[:3]:
                    name = s.get("concept_name") or s.get("concept_id", "unknown")
                    if s.get("is_cold_start"):
                        lines.append(f"  - **{name}**: not yet studied")
                    else:
                        score = round(s.get("proficiency_score", 0.0), 2)
                        lines.append(f"  - **{name}**: {score}")

            result["response"]   = "\n".join(lines)
            result["agent_used"] = "diagnostic"

        return result

    except Exception as exc:
        return {"error": f"Diagnostic Agent failed: {exc}"}
