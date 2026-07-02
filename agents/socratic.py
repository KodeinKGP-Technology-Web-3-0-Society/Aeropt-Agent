"""
Socratic Agent

Fires INSTEAD of Solver when the routing condition from Section 8 is met:
  prereq_score > 0.7 AND target_score < 0.4 AND NOT cold_start
  AND socratic_turn_count < SOCRATIC_MAX_TURNS

Generates a guiding question to draw out the student's existing knowledge
rather than giving a direct answer.

Auto-escalation: if socratic_turn_count >= SOCRATIC_MAX_TURNS → Solver takes over
(handled in graph/workflow.py routing; this node only fires when allowed).

Model: llama-3.3-70b-versatile (Groq) — best conversational reasoning on Groq
"""

from __future__ import annotations

from typing import Any, Dict, List

from groq import Groq

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import groq_complete

_groq = Groq(api_key=config.GROQ_API_KEY)

_SOCRATIC_PROMPT = """\
You are a Socratic tutor for JEE Physics. Your job is to guide the student to
the answer through targeted questions, NOT to give the answer directly.

── Context ──────────────────────────────────────────────────────────────────
Concept being studied: {concept_name}
Student proficiency:   {proficiency:.2f} / 1.0  (they know the prerequisites well)
Socratic turn:         {turn_num} of {max_turns}

── Retrieved content (use only to frame your question, do NOT quote it) ─────
{chunks_summary}

── Conversation so far this session ─────────────────────────────────────────
{recent_turns}

── Previous Socratic questions asked this session (DO NOT REPEAT THESE) ─────
{previous_socratic_questions}

── Student's current message ────────────────────────────────────────────────
{query}

── Instructions ─────────────────────────────────────────────────────────────
• Ask exactly ONE focused question that nudges the student one step closer to
  the answer.
• The question should connect to something the student already knows
  (they have strong prerequisites).
• Keep it short (1-3 sentences).
• If this is turn {max_turns} (final allowed Socratic turn), end your question
  with: "If you're unsure, tell me and I'll explain directly."
• Use LaTeX for any mathematical expression."""


def _get_previous_socratic_questions(
    session_id: str, concept_id: str
) -> List[str]:
    """Retrieve assistant messages from prior Socratic turns for this concept."""
    turns = db.read_all_chat_turns(session_id)
    questions = []
    for t in turns:
        if (
            t.get("agent_used") == "socratic"
            and t.get("role") == "assistant"
            and concept_id in (t.get("concepts_referenced") or [])
        ):
            questions.append(t["content"][:300])
    return questions


def _chunks_summary(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return "(no specific content retrieved)"
    lines = []
    for c in chunks[:3]:  # summarise top 3 only
        text = (c.get("core_text") or "")[:200]
        lines.append(f"• {text}")
    return "\n".join(lines)


def _format_recent_turns(turns: List[Dict[str, Any]]) -> str:
    if not turns:
        return "  (start of session)"
    lines = []
    for t in turns:
        lines.append(f"  [{t.get('role', '')}]: {t.get('content', '')[:250]}")
    return "\n".join(lines)


def socratic_node(state: TutorState) -> Dict[str, Any]:
    try:
        primary_id = state.get("primary_concept_id", "")
        target_state = state.get("target_concept_state") or {}
        chunks = state.get("retrieved_chunks", [])
        recent_turns = state.get("recent_turns", [])
        turn_num = state.get("socratic_turn_count", 0) + 1

        # Concept name from chunks or fall back to id
        concept_name = (chunks[0].get("primary_concept_id") if chunks else None) or primary_id

        prior_questions = _get_previous_socratic_questions(
            state["session_id"], primary_id
        )
        prior_q_text = (
            "\n".join(f"  • {q}" for q in prior_questions)
            if prior_questions
            else "  (none yet)"
        )

        prompt = _SOCRATIC_PROMPT.format(
            concept_name=concept_name,
            proficiency=target_state.get("proficiency_score", 0.5),
            turn_num=turn_num,
            max_turns=config.SOCRATIC_MAX_TURNS,
            chunks_summary=_chunks_summary(chunks),
            recent_turns=_format_recent_turns(recent_turns),
            previous_socratic_questions=prior_q_text,
            query=state["query"],
        )

        raw, model_used = groq_complete(
            client=_groq,
            model=config.SOCRATIC_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SOCRATIC_FALLBACK_MODELS,
            temperature=0.5,
            max_tokens=400,
        )
        response = raw.strip()

        # Persist the assistant turn tagged as "socratic"
        db.insert_chat_turn(
            session_id=state["session_id"],
            role="assistant",
            content=response,
            turn_index=state.get("interaction_count", 1) * 2,
            agent_used="socratic",
            concepts_referenced=[primary_id] if primary_id else None,
        )

        return {
            "response": response,
            "agent_used": "socratic",
            "model_used": model_used,
            "socratic_turn_count": turn_num,
            "error": None,
        }

    except Exception as exc:
        return {"error": f"Socratic Agent failed: {exc}"}
