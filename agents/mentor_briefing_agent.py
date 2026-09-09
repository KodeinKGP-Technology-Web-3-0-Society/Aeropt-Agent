"""Mentor Briefing product agent — human-readable summaries for mentors."""

from __future__ import annotations

from typing import Any, Dict

from agents.context_manager import context_manager_node
from db.supabase_client import db
from mcp_tools.build_mentor_briefing import BuildMentorBriefingArgs, build_mentor_briefing
from models.state import TutorState


def mentor_briefing_agent_node(state: TutorState) -> Dict[str, Any]:
    user_id = state["user_id"]
    session_id = state.get("session_id", "")

    if state.get("run_context_manager"):
        try:
            context_manager_node(state)
        except Exception:
            pass

    briefing = build_mentor_briefing(
        BuildMentorBriefingArgs(
            user_id=user_id,
            session_id=session_id or None,
        )
    )
    summary = briefing.get("summary") or "No briefing available yet."

    db.insert_chat_turn(
        session_id=session_id,
        role="assistant",
        content=summary,
        turn_index=state.get("interaction_count", 1) * 2,
        agent_used="orchestrator",
    )

    return {
        "active_agent": "mentor_briefing",
        "agent_used": "mentor_briefing",
        "response": summary,
        "agent_signals": {"mentor_briefing": briefing},
    }
