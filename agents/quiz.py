"""
Quiz Agent

Triggered ONLY by Diagnostic when struggle_streak >= STRUGGLE_STREAK_QUIZ_TRIGGER.
This is the only agent triggered by another agent's write, not by Orchestrator.

Responsibilities:
  1. Read user_concept_state — find the struggling concept
  2. Neo4j: REQUIRES chain → decompose into prerequisite sub-concepts for steps
  3. Generate a 3-5 step micro-quiz (config.QUIZ_MODEL)
     Each step tests one prerequisite concept building up to the target
  4. Write session active_quiz = True
  5. Return quiz_steps list for the caller to administer step-by-step

Each step gets its own Diagnostic call when the student answers it (orchestrated
by main.py / workflow, not here).

Model: config.QUIZ_MODEL (Groq) — strong multi-step reasoning
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from openai import OpenAI

from utils.llm import complete_text

# ── Think-block removal (same model as Solver) ────────────────────────────────
_THINK_RE = re.compile(
    r"<think(?:ing)?>.*?</think(?:ing)?>|<think(?:ing)?>.*",
    flags=re.DOTALL | re.IGNORECASE,
)

def _clean_output(raw: str) -> str:
    return _THINK_RE.sub("", raw).strip()

import config
from db.supabase_client import db
from retrieval.curriculum_graph import graph_client  # backend-aware: Neo4j when hosted, the local index when not
from models.state import TutorState
# Reuse Solver's image + formulae helpers so quiz content renders identically.
from agents.solver import _image_urls, _formulae_block, _render_math_text

_llm_client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

# ── On-demand quiz batch (intent=quiz_request) ────────────────────────────────

_QUIZ_BATCH_SEPARATOR = (
    "\n\n"
    "─────────────── ATTEMPT ALL QUESTIONS ABOVE BEFORE SCROLLING ───────────────"
    "\n\n"
)


def _format_quiz_batch(chunks: List[Dict[str, Any]]) -> str:
    """
    Deterministically assemble an on-demand quiz from retrieved practice/PYQ
    chunks — NO LLM.  Layout:

        **Question 1** ... (image)
        **Question 2** ...
        ──────── ATTEMPT ALL BEFORE SCROLLING ────────
        **Answer 1** ... (formulae, image)
        **Answer 2** ...
        ---
        ---
        (Grading via Correct/Incorrect buttons in the app.)
    """
    n = len(chunks)
    parts: List[str] = [f"Here is your {n}-question quiz. Attempt all of them first, "
                        "then scroll down to self-check.\n"]

    # ── Questions ─────────────────────────────────────────────────────────────
    for i, c in enumerate(chunks, 1):
        q = (c.get("core_text") or "").strip() or "*(Question text not available.)*"
        diff = c.get("difficulty")
        diff_tag = f"  _(difficulty {diff}/5)_" if diff else ""
        parts.append(f"\n\n**Question {i}**{diff_tag}\n\n{q}")
        core_url = _image_urls(c)["core"]
        if core_url:
            parts.append(f"\n\n![Question {i} diagram]({core_url})")

    parts.append(_QUIZ_BATCH_SEPARATOR)

    # ── Answers ───────────────────────────────────────────────────────────────
    for i, c in enumerate(chunks, 1):
        raw_sol = (c.get("solution") or "").strip()
        sol = _render_math_text(raw_sol) if raw_sol else "*(Solution not available.)*"
        parts.append(f"\n\n**Answer {i}**\n\n{sol}")
        sol_url = _image_urls(c)["solution"]
        if sol_url:
            parts.append(f"\n\n![Answer {i} diagram]({sol_url})")
        block = _formulae_block([c], exclude_text=sol)
        if block:
            parts.append(f"\n\n_Key formulae:_\n\n{block}")

    return "".join(parts)


def _quiz_batch_node(state: TutorState) -> Dict[str, Any]:
    """Build an on-demand quiz batch from the retrieved chunks (no LLM)."""
    session_id = state["session_id"]
    primary_id = state.get("primary_concept_id", "")
    chunks = state.get("retrieved_chunks", []) or []
    # Honour the requested count (1–5, default 5); use whatever was retrieved if
    # fewer exist — no backfill from prereqs.
    size = state.get("quiz_batch_size") or 5
    size = max(1, min(5, size))
    chunks = chunks[:size]

    if not chunks:
        msg = (
            "I couldn't find quiz questions for that topic right now. "
            "Try asking for a specific concept, e.g. \"quiz me on torque\"."
        )
        return {
            "response": msg,
            "agent_used": "quiz",
            "quiz_batch_mode": False,
            "awaiting_graded_response": False,
            "error": None,
        }

    response = _format_quiz_batch(chunks)
    return {
        "response": response,
        "agent_used": "quiz",
        "quiz_batch_mode": True,
        "quiz_batch_index": 0,                    # Question 1 is graded next
        "quiz_batch_chunks": chunks,
        # Grade Q1 against the first chunk; content_type keeps 1/0 detection on.
        "retrieved_chunks": [chunks[0]],
        "content_type_requested": "practice",
        "awaiting_graded_response": True,
        "error": None,
    }

_QUIZ_PROMPT = """\
You are a JEE Physics quiz designer. Create a micro-quiz to help a struggling student
rebuild their understanding from fundamentals.

── Struggling concept ─────────────────────────────────────────────────────────
{concept_id}

── Prerequisite chain (leaf → direct prereq → target) ──────────────────────
{prereq_chain}

── Student's weak points ─────────────────────────────────────────────────────
Proficiency: {proficiency:.2f}/1.0
Error pattern: {error_summary}
Struggle streak: {struggle_streak} consecutive wrong answers

── Instructions ──────────────────────────────────────────────────────────────
Design a 3–5 step micro-quiz that:
1. Starts with the deepest prerequisite concept (easiest, foundational)
2. Builds up step-by-step to the struggling target concept
3. Each step is a SHORT answer question (not MCQ)
4. Include a hint per step (one sentence, does NOT give away the answer)
5. Include the correct answer per step

Return ONLY valid JSON (no markdown fences):
{{
  "quiz_steps": [
    {{
      "step_num": 1,
      "concept_id": "<concept_id being tested>",
      "question": "<question text, use LaTeX for math>",
      "hint": "<one-sentence hint>",
      "answer": "<correct answer>"
    }},
    ...
  ]
}}"""


def _error_summary(cs: Dict[str, Any]) -> str:
    dist = cs.get("error_type_dist") or {}
    total = sum(dist.values())
    if total == 0:
        return "none recorded"
    dominant = max(dist, key=lambda k: dist[k])
    return f"mostly {dominant} ({int(100*dist[dominant]/total)}%)"


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # Try to extract the JSON object
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


def quiz_node(state: TutorState) -> Dict[str, Any]:
    # On-demand quiz batch (student explicitly asked) — deterministic, no LLM.
    if state.get("quiz_batch_mode"):
        try:
            return _quiz_batch_node(state)
        except Exception as exc:
            return {"error": f"Quiz Agent failed (batch): {exc}"}

    # Otherwise: struggle-triggered LLM micro-quiz (existing behaviour).
    user_id = state["user_id"]
    session_id = state["session_id"]
    primary_id = state.get("primary_concept_id", "")

    try:
        # 1. Load concept state for the struggling concept
        cs = db.read_user_concept_state(user_id, primary_id)
        if not cs:
            return {"error": f"No concept state for {user_id}/{primary_id}"}

        # 2. Neo4j: get prerequisite chain for step decomposition
        prereq_chain = graph_client().get_requires_chain_for_quiz(primary_id, max_depth=2)

        prereq_text_lines = [
            f"  depth={p['depth']}: {p['concept_id']} ({p.get('concept_name', '')})"
            for p in prereq_chain
        ]
        prereq_text = "\n".join(prereq_text_lines) if prereq_text_lines else "  (no prerequisites found)"

        prompt = _QUIZ_PROMPT.format(
            concept_id=primary_id,
            prereq_chain=prereq_text,
            proficiency=cs.get("proficiency_score", 0.5),
            error_summary=_error_summary(cs),
            struggle_streak=cs.get("struggle_streak", 0),
        )

        raw_text, _ = complete_text(
            client=_llm_client,
            model=config.QUIZ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.QUIZ_FALLBACK_MODELS,
            temperature=0.3,
            max_tokens=1200,
        )
        raw = _clean_output(raw_text)

        # Same empty-think-block guard as Solver
        if len(raw) < 20:
            raw_text, _ = complete_text(
                client=_llm_client,
                model=config.QUIZ_FALLBACK_MODELS[0],
                messages=[{"role": "user", "content": prompt}],
                fallback_models=config.QUIZ_FALLBACK_MODELS[1:],
                temperature=0.3,
                max_tokens=1200,
            )
            raw = _clean_output(raw_text)

        parsed = _extract_json(raw)
        quiz_steps: List[Dict[str, Any]] = parsed.get("quiz_steps", [])

        # 3. Validate and cap at 5 steps
        quiz_steps = quiz_steps[:5]
        for i, step in enumerate(quiz_steps, 1):
            step["step_num"] = i
            step.setdefault("concept_id", primary_id)

        # 4. Mark session as having active quiz
        db.update_session_active_quiz(session_id, active=True)

        return {
            "quiz_steps": quiz_steps,
            "active_quiz": True,
            "error": None,
        }

    except Exception as exc:
        return {"error": f"Quiz Agent failed: {exc}"}

def quiz_builder_node(state: TutorState) -> Dict[str, Any]:
    """
    Wrapper for quiz generation that tries to serve verified DB content first.
    If no valid content is found in the retrieved chunks, falls back to generation.
    """
    chunks = state.get("retrieved_chunks", []) or []
    
    valid_chunks = [
        c for c in chunks
        if (c.get("content_type") or "").lower() in {"practice question", "pyq", "practice"}
        and bool((c.get("solution") or "").strip())
    ]
    
    if valid_chunks:
        # Force quiz_batch_mode to use the deterministic quiz builder logic
        new_state = dict(state)
        new_state["quiz_batch_mode"] = True
        return _quiz_batch_node(new_state)

    # Fall back to LLM generation
    result = quiz_node(state)
    
    if not result.get("error") and "quiz_steps" in result:
        # Just add a flag for the frontend/agent, and optionally append to the message
        # But wait, quiz_node inserts its own "[Quiz started..." chat turn.
        # We don't really have a 'response' string returned here besides quiz_steps.
        # The Orchestrator / Learning Agent will see the steps.
        # Let's add a flag `generated_quiz: True` so the agent knows.
        result["generated_quiz"] = True
        
    return result
