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

        session_summary: Optional[str] = None
        structured: Optional[Dict[str, Any]] = None
        if args.session_id:
            structured = db.read_structured_memory(args.session_id)
            session_summary = db.read_session_summary(args.session_id)
            if structured and structured.get("text_summary"):
                session_summary = structured["text_summary"]

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

        summary = " ".join(lines)
        return {
            "summary": summary,
            "weak_concepts": weak,
            "strong_concepts": strong,
            "structured_memory": structured,
            "goal_minutes": args.goal_minutes,
        }
    except Exception as exc:
        return {
            "summary": "Briefing unavailable right now. Please try again shortly.",
            "error": str(exc),
        }
