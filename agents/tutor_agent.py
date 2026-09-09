"""Tutor product agent — teaches via solver, socratic, and graph retrieval."""

from __future__ import annotations

from typing import Any, Dict

from models.state import TutorState


def tutor_agent_node(state: TutorState) -> Dict[str, Any]:
    """Marker node: orchestrator routed here for direct teaching flows."""
    return {
        "active_agent": "tutor",
        "agent_used": state.get("agent_used") or "tutor",
    }
