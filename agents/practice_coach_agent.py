"""Practice Coach product agent — drills, PYQs, quizzes, and grading loops."""

from __future__ import annotations

from typing import Any, Dict

from models.state import TutorState


def practice_coach_agent_node(state: TutorState) -> Dict[str, Any]:
    content_type = state.get("content_type_requested") or "practice"
    intent = state.get("intent", "")
    if intent == "quiz_request":
        content_type = "practice"
    sandbox = (state.get("sandbox_action") or "").upper()
    if sandbox == "PYQ":
        content_type = "pyq"
    elif sandbox in ("PRACTICE", "QUIZ"):
        content_type = "practice"

    return {
        "active_agent": "practice_coach",
        "agent_used": "practice_coach",
        "content_type_requested": content_type,
    }
