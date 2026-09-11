"""Deterministic turn setup. No model runs here.

This is what remains of the old Orchestrator once its intent classification is
removed. Everything left is bookkeeping that has exactly one right answer —
load the profile, resolve the session, count the turn, record what the student
said — so none of it belongs in a reasoning loop. Running a classifier to
decide *what kind of turn this is* was the part that never worked, because the
kinds were a closed list of seven and real students do not stay inside it.

The agent reads the context this assembles and decides everything else.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import config
from db.supabase_client import db
from models.state import TutorState

logger = logging.getLogger("jee_tutor.bootstrap")


def session_bootstrap_node(state: TutorState) -> Dict[str, Any]:
    """Load profile and session context, and record the student's turn."""
    user_id = state["user_id"]
    session_id = state["session_id"]
    query = state.get("query") or ""

    try:
        user = db.read_user(user_id)
        if not user:
            return {"error": f"User {user_id} not found. Please register first."}

        focus_chapters = list(state.get("focus_chapters") or [])
        if not focus_chapters:
            try:
                profile = db.read_user_learning_profile(user_id)
                if profile:
                    focus_chapters = list(profile.get("weak_chapters") or [])
            except Exception:
                logger.debug("learning profile lookup failed", exc_info=True)

        session = db.read_session(session_id)
        if not session:
            session = db.create_session(user_id)
            session_id = session["session_id"]
        interaction_count = (session.get("interaction_count") or 0) + 1

        recent_turns = db.read_recent_chat_turns(session_id, n=4)

        db.update_session_interaction_count(session_id, interaction_count)

        # Record the student's message before the agent runs, so a turn that
        # fails mid-flight still leaves the conversation intact.
        db.insert_chat_turn(
            session_id=session_id,
            role="user",
            content=query,
            turn_index=interaction_count * 2 - 1,   # odd indices = user
            agent_used="student",
        )

        session_summary = None
        prior_session_summary = state.get("prior_session_summary")
        try:
            session_summary = db.read_session_summary(session_id)
            if interaction_count == 1 and prior_session_summary is None:
                prior_session_summary = db.read_prior_session_summary(
                    user_id, exclude_session_id=session_id
                )
        except Exception:
            logger.debug("summary lookup failed", exc_info=True)

        return {
            "session_id": session_id,
            "target_exam": user.get("target_exam", "JEE_ADVANCED"),
            "preferred_depth": user.get("preferred_depth", "standard"),
            "grade_level": user.get("grade_level"),
            "daily_goal_minutes": user.get("daily_goal_minutes"),
            "focus_chapters": focus_chapters,
            "interaction_count": interaction_count,
            "active_quiz": session.get("active_quiz", False),
            "recent_turns": recent_turns,
            "session_summary": session_summary,
            "prior_session_summary": prior_session_summary,
            "run_context_manager": (
                interaction_count % config.CONTEXT_MANAGER_EVERY_N == 0
            ),
            "error": None,
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("Session bootstrap failed")
        return {"error": f"Session bootstrap failed: {exc}"}
