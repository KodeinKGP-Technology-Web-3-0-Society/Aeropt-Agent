"""
MCP Tool: Get Agent Signals
Computes the five product-agent card signals for the frontend roster panel.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import config
from db.supabase_client import db
from mcp_tools.get_syllabus_status import GetSyllabusStatusArgs, get_syllabus_status


class GetAgentSignalsArgs(BaseModel):
    user_id: str = Field(..., description="Supabase user UUID.")
    session_id: Optional[str] = Field(default=None, description="Active chat session, if any.")
    focus_chapters: List[str] = Field(default_factory=list)
    roadmap_concept_id: Optional[str] = Field(default=None)
    active_agent: Optional[str] = Field(default=None, description="Currently active product agent.")
    primary_concept_id: Optional[str] = Field(default=None)


AGENT_CATALOG: List[Dict[str, str]] = [
    {
        "id": "tutor",
        "name": "Tutor agent",
        "description": "Teaches concepts, asks Socratic questions, and explains with rendered LaTeX.",
    },
    {
        "id": "syllabus",
        "name": "Syllabus agent",
        "description": "Keeps you aligned with your roadmap, prerequisites, and due reviews.",
    },
    {
        "id": "doubt_resolver",
        "name": "Doubt resolver",
        "description": "Triages misconceptions and routes you to the right teaching mode.",
    },
    {
        "id": "practice_coach",
        "name": "Practice coach",
        "description": "Runs drills, PYQs, quizzes, and the self-grade feedback loop.",
    },
    {
        "id": "mentor_briefing",
        "name": "Mentor briefing",
        "description": "Summarises progress for mentors and guardians in plain language.",
    },
]


def _concept_display_name(concept_id: Optional[str], concepts: Dict[str, Dict[str, Any]]) -> str:
    if not concept_id:
        return "your study plan"
    row = concepts.get(concept_id) or {}
    return row.get("concept_name") or concept_id.replace("_", " ")


def get_agent_signals(args: GetAgentSignalsArgs) -> Dict[str, Any]:
    """Return roster cards with status + signal text for each product agent."""
    try:
        states = db.read_all_user_concept_states(args.user_id)
        concepts = {c["concept_id"]: c for c in db.read_concepts()}
        syllabus = get_syllabus_status(
            GetSyllabusStatusArgs(
                user_id=args.user_id,
                focus_chapters=args.focus_chapters,
                roadmap_concept_id=args.roadmap_concept_id,
            )
        )

        primary = args.primary_concept_id or args.roadmap_concept_id
        primary_state = next((s for s in states if s.get("concept_id") == primary), None)
        mastery_pct = 0
        if primary_state:
            mastery_pct = int(round(float(primary_state.get("proficiency_score") or 0) * 100))

        open_doubts = sum(
            1 for s in states
            if int(s.get("struggle_streak") or 0) > 0 or bool(s.get("flagged_weak"))
        )
        escalations = sum(1 for s in states if bool(s.get("flagged_weak")))

        session_label = _concept_display_name(primary, concepts)
        active = args.active_agent or "tutor"

        # Syllabus agent: name the actual weak chapter or recommended next
        # concept when one exists, instead of a bare count — the data was
        # already being computed by get_syllabus_status(), just not surfaced.
        weak_chapters = syllabus.get("weak_chapters") or []
        next_concept_name = syllabus.get("next_concept_name")
        if weak_chapters:
            syllabus_signal = f"{weak_chapters[0]} needs review"
        elif next_concept_name:
            syllabus_signal = f"Next up: {next_concept_name}"
        else:
            unfinished = syllabus.get("unfinished_lessons", 0)
            syllabus_signal = f"{unfinished} unfinished lesson{'s' if unfinished != 1 else ''}"

        # Doubt resolver: name the single concept the student is struggling
        # with most (highest struggle_streak) rather than just counting how
        # many are flagged — that's the one worth actually pointing at.
        struggling = [s for s in states if int(s.get("struggle_streak") or 0) > 0]
        struggling.sort(key=lambda s: int(s.get("struggle_streak") or 0), reverse=True)
        if struggling:
            worst = struggling[0]
            worst_name = _concept_display_name(worst.get("concept_id"), concepts)
            streak = int(worst.get("struggle_streak") or 0)
            doubt_signal = f"Stuck on {worst_name} ({streak} in a row)"
        else:
            doubt_signal = f"{open_doubts} open doubt signal{'s' if open_doubts != 1 else ''}"

        signal_map = {
            "tutor": {
                "signal": f"{session_label} session",
                "signal_type": "session",
            },
            "syllabus": {
                "signal": syllabus_signal,
                "signal_type": "count",
            },
            "doubt_resolver": {
                "signal": doubt_signal,
                "signal_type": "count",
            },
            "practice_coach": {
                "signal": f"{mastery_pct}% mastery on {session_label}",
                "signal_type": "mastery",
            },
            "mentor_briefing": {
                "signal": f"{escalations} escalation signal{'s' if escalations != 1 else ''}",
                "signal_type": "count",
            },
        }

        agents: List[Dict[str, Any]] = []
        for meta in AGENT_CATALOG:
            aid = meta["id"]
            sig = signal_map[aid]
            agents.append({
                **meta,
                "status": "active" if aid == active else "idle",
                **sig,
            })

        return {"agents": agents, "active_agent": active}
    except Exception as exc:
        fallback = []
        for meta in AGENT_CATALOG:
            fallback.append({**meta, "status": "idle", "signal": "—", "signal_type": "count"})
        return {"agents": fallback, "active_agent": args.active_agent, "error": str(exc)}
