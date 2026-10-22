"""The Evaluation Agent: runs after the practice phase to compute outcomes.

This agent reads the study_session_events, computes performance summaries,
updates readiness scores, and generates a human-readable evaluation summary.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import config
from db.supabase_client import db
from models.study_state import StudyRoomState
from mcp_tools.study_tools import compute_readiness_score
from utils.llm import complete_text

logger = logging.getLogger("jee_tutor.evaluation_agent")

_EVAL_PROMPT = """You are a JEE Physics tutor evaluating a student's performance after a study session.

Concept: {concept_name}
Difficulty Reached: {difficulty}/5
Questions Attempted: {questions_served}
Questions Correct: {questions_correct} (Correct Rate: {correct_rate}%)

Recent events (oldest first):
{events_log}

Write a brief, encouraging performance summary for the student.
1. Start with the overall score (e.g. "You got 4 out of 5 right").
2. Highlight one strength you observed in their correct answers.
3. Highlight one area they need to work on, based on their incorrect answers or doubts.
4. Keep it under 100 words. Address the student directly ("You...").
Use plain markdown; LaTeX only for a formula, never around words.
"""


def evaluation_agent_node(state: StudyRoomState) -> Dict[str, Any]:
    """Evaluate the student's performance across the entire study session."""
    user_id = state.get("user_id") or ""
    ssid = state.get("study_session_id") or ""
    concept_id = state.get("concept_id") or ""
    
    if not user_id or not ssid or not concept_id:
        return {"error": "Missing core identifiers for evaluation."}

    from services import progress

    progress.report("stage", "evaluation")
    # 1. Read study session events
    events = db.read_study_events(ssid)
    
    # 2. Get current session stats
    session = db.read_study_session(ssid)
    if not session:
        return {"error": "Study session not found."}
        
    difficulty = session.get("difficulty_level", 1)
    served = session.get("questions_served", 0)
    correct = session.get("questions_correct", 0)
    rate = round((correct / served) * 100) if served > 0 else 0

    # 3. Compute readiness score using the deterministic tool
    old_readiness = db.read_concept_readiness(user_id, concept_id)
    old_score = float(old_readiness.get("score", 0)) if old_readiness else 0.0

    new_readiness = compute_readiness_score(user_id, concept_id)
    new_score = new_readiness.get("score")
    if new_score is None:
        new_score = None
    else:
        db.upsert_readiness_score(
            user_id=user_id,
            concept_id=concept_id,
            score=new_score,
            confidence=new_readiness["confidence"],
            factors=new_readiness["factors"],
        )

    # 4. Generate LLM summary if there was practice
    summary = "You completed the lesson, but haven't answered any practice questions yet."
    
    if served > 0:
        # Build event log for LLM
        log_lines = []
        for e in events[-20:]:  # Last 20 events
            etype = e.get("event_type")
            pay = e.get("payload") or {}
            if etype == "question_attempted":
                status = "Correct" if pay.get("is_correct") else "Incorrect"
                ans = pay.get("student_answer") or "none"
                err = f", {pay['error_type']} error" if pay.get("error_type") and not pay.get("is_correct") else ""
                log_lines.append(f"Attempted Q (difficulty {pay.get('difficulty')}): {status}{err} (Answered: {ans})")
            elif etype == "doubt_asked":
                log_lines.append(f"Asked doubt: {pay.get('doubt_text', '')}")
            elif etype == "difficulty_adjusted":
                log_lines.append(f"Difficulty changed to {pay.get('new_difficulty')}")
        
        prompt = _EVAL_PROMPT.format(
            concept_name=state.get("concept_name", concept_id),
            difficulty=difficulty,
            questions_served=served,
            questions_correct=correct,
            correct_rate=rate,
            events_log="\n".join(log_lines) or "No detailed events.",
        )
        
        try:
            # This used to call chat_completion(system_prompt=…, user_message=…), which
            # is not its signature — it raised every time, so every student got
            # the one-line fallback instead of a summary.
            summary, _ = complete_text(
                client=None,  # the key pool supplies the client
                model=config.ORCHESTRATOR_MODEL,
                messages=[
                    {"role": "system", "content": "You are an expert tutor summarising a study session."},
                    {"role": "user", "content": prompt},
                ],
                fallback_models=config.SMALL_MODEL_FALLBACK,
                temperature=0.3,
                max_tokens=2000,
                min_chars=40,
                lane="background",
            )
            summary = summary.strip()
        except Exception as e:
            logger.warning(f"Evaluation summary generation failed: {e}")
            summary = f"You got {correct} out of {served} questions correct at difficulty level {difficulty}."

    db.update_study_session_fields(ssid, {"evaluation_summary": summary})
    progress.report("stage", "evaluation", "done")
    # 5. Return updates
    return {
        "evaluation_summary": summary,
        "readiness_score": new_score,
        "readiness_available": new_score is not None,
        "readiness_delta": None if new_score is None else new_score - old_score,
        # The teacher's reply for this turn (feedback on the last answer) stays
        # the chat message; the summary is shown on the results card.
        "response": state.get("response") or summary,
        "outcome": session.get("outcome"),
        "agent_used": "evaluation_agent",
        "model_used": config.ORCHESTRATOR_MODEL,
        "active_agent": "evaluation_agent",
    }
