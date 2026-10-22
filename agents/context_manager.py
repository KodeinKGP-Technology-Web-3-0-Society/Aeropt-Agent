"""
Rolling conversation summarization stage.

Runs synchronously when the graph routes a turn here after bootstrap marks
run_context_manager, or when the shared session-summary flush calls it.

Responsibilities:
  1. Read all unsummarized chat_turns for the session (ordered by turn_index)
  2. Read the existing session_summary (may be None or a prior summary)
  3. Compress: produce a rolling summary that replaces the old one
  4. Write sessions.session_summary
  5. Mark the summarized chat_turns.is_summarized = True

The running summary is cumulative — it captures all turns up to now,
not just the latest window.  This keeps long sessions within token budget.

Model: config.CONTEXT_MANAGER_MODEL through the shared model transport.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from openai import OpenAI

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import complete_text

logger = logging.getLogger("jee_tutor.context_manager")

_llm_client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

_SUMMARY_PROMPT = """\
You are a session summariser for a JEE Physics tutoring system.
Produce a concise but complete rolling summary of this tutoring session.

── Existing summary (from previous summarisation, may be empty) ─────────────
{existing_summary}

── New turns to incorporate (ordered, oldest first) ──────────────────────────
{turns_text}

── Instructions ─────────────────────────────────────────────────────────────
• Merge the existing summary with the new turns into ONE updated summary.
• Include: concepts covered, key explanations given, student's performance
  (right/wrong answers if any), notable errors, and any ongoing quiz state.
• Be factual and concise — bullet points are fine.
• Max length: ~300 words.
• Write the summary only; no preamble."""


def _format_turns(turns: List[Dict[str, Any]]) -> str:
    lines = []
    for t in turns:
        role = t.get("role", "?")
        agent = t.get("agent_used", "")
        text = (t.get("content") or "")[:400]
        tag = f" [{agent}]" if agent else ""
        lines.append(f"[Turn {t.get('turn_index', '?')} | {role}{tag}]: {text}")
    return "\n".join(lines) if lines else "(no turns)"


def context_manager_node(state: TutorState) -> Dict[str, Any]:
    session_id = state["session_id"]

    try:
        # 1. Load unsummarized turns
        unsummarized = db.read_unsummarized_turns(session_id)
        if not unsummarized:
            return {}  # Nothing to do

        # 2. Load existing summary
        existing_summary = db.read_session_summary(session_id) or "(none)"

        # 3. Generate new rolling summary
        prompt = _SUMMARY_PROMPT.format(
            existing_summary=existing_summary,
            turns_text=_format_turns(unsummarized),
        )

        raw, _ = complete_text(
            client=_llm_client,
            model=config.CONTEXT_MANAGER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.2,
            max_tokens=500,
        )
        new_summary = raw.strip()

        # 4. Write summary back to session
        db.update_session_summary(session_id, new_summary)

        # 5. Mark turns as summarized
        turn_ids = [t["turn_id"] for t in unsummarized if t.get("turn_id")]
        db.mark_turns_summarized(turn_ids)

        return {"error": None}

    except Exception as exc:
        logger.warning("Context manager failed; the reply still stands: %s", exc)
        return {}
