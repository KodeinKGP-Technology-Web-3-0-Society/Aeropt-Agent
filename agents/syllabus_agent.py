"""Syllabus product agent — roadmap alignment and scope before teaching."""

from __future__ import annotations

from typing import Any, Dict

from mcp_tools.get_syllabus_status import GetSyllabusStatusArgs, get_syllabus_status
from models.state import TutorState


def syllabus_agent_node(state: TutorState) -> Dict[str, Any]:
    status = get_syllabus_status(
        GetSyllabusStatusArgs(
            user_id=state["user_id"],
            focus_chapters=state.get("focus_chapters") or [],
            roadmap_concept_id=state.get("roadmap_concept_id") or state.get("primary_concept_id"),
        )
    )
    updates: Dict[str, Any] = {
        "active_agent": "syllabus",
        "agent_used": "syllabus",
        "delegation_target": "tutor",
        "agent_signals": {"syllabus": status},
    }
    if status.get("next_concept_id") and not state.get("primary_concept_id"):
        updates["primary_concept_id"] = status["next_concept_id"]
    return updates
