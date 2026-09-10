"""
Socratic Agent

Fires INSTEAD of Solver when the routing condition from Planner is met:
  teaching_mode == "socratic"

Generates a guiding question to draw out the student's existing knowledge
rather than giving a direct answer.

Auto-escalation: Evaluates lack of progress (proficiency delta) to decide when to yield to direct explanation, replacing the old fixed 3-turn limit.

Model: config.SOCRATIC_MODEL (Groq) — best conversational reasoning available on this account
"""

from __future__ import annotations

from typing import Any, Dict, List

from groq import Groq

from agents.solver import prepare_student_markdown

import config
from db.supabase_client import db
from models.state import TutorState
from utils.latex_guidelines import LATEX_OUTPUT_RULES, sanitize_chunk_text_for_prompt
from utils.llm import groq_complete

_groq = Groq(api_key=config.GROQ_API_KEY)

_SOCRATIC_PROMPT = """\
You are a Socratic tutor for JEE Physics. Your job is to guide the student to
the answer through targeted questions, NOT to give the answer directly.

── Context ──────────────────────────────────────────────────────────────────
Concept being studied: {concept_name}
Student proficiency:   {proficiency:.2f} / 1.0  (they know the prerequisites well)
Pedagogical framing:   {framing}

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
• ESCALATION RULE: If the student is showing a lack of progress (e.g., their proficiency score is dropping in recent diagnostic turns, or they are clearly frustrated), you MUST escalate. End your question with: "If you're unsure, tell me and I'll explain directly."
"""


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
        return (
            "(no knowledge-graph chunks — frame the question from the concept, "
            "proficiency, and conversation. Retrieved content is optional.)"
        )
    lines = []
    for c in chunks[:3]:  # summarise top 3 only
        text = sanitize_chunk_text_for_prompt((c.get("core_text") or "")[:200])
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

        from utils.skills import load_skill
        skill = load_skill("physics_socratic") or {}

        framing = state.get("framing", "deepen")
        framing_desc = skill.get("framing_hints", {}).get(framing, "Guide the student through the concept.")

        prompt = skill["system_prompt"]
        prompt = prompt.replace("{{concept_name}}", str(concept_name))
        prompt = prompt.replace("{{proficiency}}", f"{target_state.get('proficiency_score', 0.5):.2f}")
        prompt = prompt.replace("{{framing_desc}}", str(framing_desc))
        prompt = prompt.replace("{{chunks_summary}}", str(_chunks_summary(chunks)))
        prompt = prompt.replace("{{recent_turns}}", str(_format_recent_turns(recent_turns)))
        prompt = prompt.replace("{{previous_socratic_questions}}", str(prior_q_text))
        prompt = prompt.replace("{{query}}", str(state["query"]))
        
        prompt = f"{prompt}\n\n{LATEX_OUTPUT_RULES}"

        raw, model_used = groq_complete(
            client=_groq,
            model=skill.get("model", config.SOCRATIC_MODEL),
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SOCRATIC_FALLBACK_MODELS,
            temperature=skill.get("temperature", 0.5),
            max_tokens=400,
            stream=True,
        )
        response = prepare_student_markdown(raw.strip())

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
