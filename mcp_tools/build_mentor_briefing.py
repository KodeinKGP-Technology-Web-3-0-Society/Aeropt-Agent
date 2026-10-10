"""
MCP Tool: Build Mentor Briefing
Produces a human-readable session / progress summary for mentors and guardians.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import config
from db.supabase_client import db


class BuildMentorBriefingArgs(BaseModel):
    user_id: str = Field(..., description="Supabase user UUID.")
    session_id: Optional[str] = Field(default=None, description="Session to summarise.")
    goal_minutes: int = Field(default=30, description="Daily study goal in minutes.")


def build_mentor_briefing(args: BuildMentorBriefingArgs) -> Dict[str, Any]:
    """
    Build a concise briefing from Supabase learning state and optional session memory.
    """
    try:
        profile = db.read_user_learning_profile(args.user_id) or {}
        states = db.read_all_user_concept_states(args.user_id)
        concepts = {c["concept_id"]: c for c in db.read_concepts()}

        weak = [
            concepts.get(s["concept_id"], {}).get("concept_name", s["concept_id"])
            for s in states
            if bool(s.get("flagged_weak"))
        ][:5]
        strong = [
            concepts.get(s["concept_id"], {}).get("concept_name", s["concept_id"])
            for s in states
            if float(s.get("proficiency_score") or 0) >= config.MASTERY_THRESHOLD
        ][:5]

        # `db.read_structured_memory` was called here until this fix. Migration
        # 003 dropped the structured_memory column and the method with it, so
        # every briefing that was given a session_id raised AttributeError,
        # landed in the broad handler below, and returned "Briefing unavailable"
        # — the failure was total but silent. The durable replacement is
        # learner_memory (migration 006), which is what this reads now.
        session_summary: Optional[str] = None
        if args.session_id:
            session_summary = db.read_session_summary(args.session_id)

        remembered = db.read_learner_memories(
            args.user_id,
            kinds=["misconception", "goal", "preference"],
            limit=5,
            min_confidence=0.5,
        )

        lines: List[str] = []
        if session_summary:
            lines.append(session_summary.strip())
        else:
            lines.append("No active session summary yet — student has not ended a recent study session.")

        if weak:
            lines.append(f"Needs support on: {', '.join(weak)}.")
        if strong:
            lines.append(f"Showing strength in: {', '.join(strong[:3])}.")

        dominant = profile.get("error_dominant_type")
        if dominant:
            lines.append(f"Most common error type lately: {dominant}.")

        goals = [m["fact"] for m in remembered if m.get("kind") == "goal"]
        if goals:
            lines.append(f"Working towards: {goals[0]}")
        misconceptions = [m["fact"] for m in remembered if m.get("kind") == "misconception"]
        if misconceptions:
            lines.append(f"Recurring difficulty: {misconceptions[0]}")

        summary = " ".join(lines)
        return {
            "summary": summary,
            "weak_concepts": weak,
            "strong_concepts": strong,
            "remembered_facts": remembered,
            "goal_minutes": args.goal_minutes,
        }
    except Exception as exc:
        return {
            "summary": "Briefing unavailable right now. Please try again shortly.",
            "error": str(exc),
        }
