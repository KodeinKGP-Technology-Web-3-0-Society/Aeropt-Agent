"""
Practice Generator Agent

Dynamically generates batches of practice questions (MCQs and Numericals)
based on the user's exam target (JEE Advanced/Mains) and current concept.
Returns the batch in `generated_practice_batch` which is picked up by the frontend canvas.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from openai import OpenAI

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import groq_complete
from utils.stream import stream_callback_var

logger = logging.getLogger("jee_tutor.practice_generator")
_groq = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

_QUESTION_TYPES = frozenset({"mcq", "numerical"})

_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}

# "1 rotational motion question" — topic words may sit between the count and noun.
_COUNT_RE = re.compile(
    r"\b([1-5]|one|two|three|four|five|a|an)\b"
    r"(?:\s+\S+){0,6}?"
    r"\s+(?:quiz\s+)?(?:questions?|qs\b|problems?|mcqs?)\b",
    flags=re.IGNORECASE,
)
_SINGULAR_RE = re.compile(r"\b(question|problem|mcq)\b", re.IGNORECASE)
_PLURAL_RE = re.compile(r"\b(questions|problems|mcqs)\b", re.IGNORECASE)
_TOPIC_FILLER_RE = re.compile(
    r"\b(give me|generate|create|i want|please|practice|a|an|one|two|three|four|five|"
    r"[1-5]|questions?|problems?|mcqs?|qs|on|for|of|the|jee|advanced|mains|level)\b",
    flags=re.IGNORECASE,
)

_PROMPT = """\
You are an expert JEE physics tutor. The student wants to practice {concept_id}.
Their target exam is {target_exam}.

Generate exactly {batch_size} practice question(s) at {target_exam} difficulty.
Do not generate more than {batch_size}. All questions must be about {concept_id}.
If {batch_size} is greater than 1, mix mcq and numerical.

Return ONLY one JSON object. No markdown, no comments, no chain-of-thought.
Keep each solution_walkthrough under 60 words. Escape every backslash in LaTeX
(write \\\\frac not \\frac). No raw newlines inside strings.

Schema:
{{
  "questions": [
    {{
      "type": "mcq",
      "difficulty": "JEE Advanced",
      "question_text": "stem with $LaTeX$",
      "options": [
        {{"id": "A", "latex": "$...$"}},
        {{"id": "B", "latex": "$...$"}},
        {{"id": "C", "latex": "$...$"}},
        {{"id": "D", "latex": "$...$"}}
      ],
      "correct_answer": "B",
      "solution_walkthrough": "short steps"
    }},
    {{
      "type": "numerical",
      "difficulty": "JEE Advanced",
      "question_text": "stem",
      "correct_answer": "12",
      "solution_walkthrough": "short steps"
    }}
  ]
}}
"""

_THINK_RE = re.compile(
    r"<think(?:ing)?>.*?</think(?:ing)?>|<think(?:ing)?>.*",
    flags=re.DOTALL | re.IGNORECASE,
)


def _parse_batch_size(query: str) -> int:
    """How many questions the student asked for (1–5). Default 3 if unspecified."""
    m = _COUNT_RE.search(query or "")
    if m:
        token = m.group(1).lower()
        n = int(token) if token.isdigit() else _NUMBER_WORDS.get(token, 3)
        return max(1, min(5, n))
    if _SINGULAR_RE.search(query or "") and not _PLURAL_RE.search(query or ""):
        return 1
    return 3


def _resolve_practice_topic(
    query: str,
    primary_concept_id: str | None,
    concepts: List[Dict[str, Any]] | None,
) -> tuple[str, str]:
    """Return (concept_id, display label). Prefer chapter name when the query names a chapter."""
    from mcp_tools.resolve_concept_id import lexical_resolve

    concepts = concepts or []
    if primary_concept_id and primary_concept_id not in {"physics concepts"}:
        row = next((c for c in concepts if c.get("concept_id") == primary_concept_id), None)
        label = (row or {}).get("concept_name") or primary_concept_id.replace("_", " ")
        return primary_concept_id, str(label)

    hit = lexical_resolve(query, concepts) if concepts else None
    if hit and hit.get("primary_concept_id"):
        cid = str(hit["primary_concept_id"])
        row = next((c for c in concepts if c.get("concept_id") == cid), None)
        qn = re.sub(r"[^a-z0-9]+", " ", (query or "").lower()).strip()
        chapter = str((row or {}).get("chapter") or "")
        chn = re.sub(r"[^a-z0-9]+", " ", chapter.lower()).strip()
        if chn and chn in qn:
            return cid, chapter
        return cid, str((row or {}).get("concept_name") or cid.replace("_", " "))

    leftover = " ".join(_TOPIC_FILLER_RE.sub(" ", query or "").split())
    label = leftover or "physics concepts"
    return (primary_concept_id or label), label


def _clean_output(raw: str) -> str:
    return _THINK_RE.sub("", raw).strip()


def _is_question(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("type") in _QUESTION_TYPES
        and isinstance(obj.get("question_text"), str)
        and bool(obj["question_text"].strip())
    )


def _escape_control_chars_in_strings(text: str) -> str:
    """Turn raw newlines/tabs inside JSON strings into valid escapes."""
    out: List[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                continue
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _repair_json_text(text: str) -> str:
    text = _clean_output(text)
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = _escape_control_chars_in_strings(text)
    return re.sub(r'(?<!\\)\\(?![/"\\bfnrtu])', r"\\\\", text)


def _questions_from_parsed(parsed: Any) -> List[Dict[str, Any]]:
    if isinstance(parsed, dict):
        raw_list = parsed.get("questions")
        if isinstance(raw_list, list):
            return [q for q in raw_list if _is_question(q)]
        if _is_question(parsed):
            return [parsed]
    if isinstance(parsed, list):
        return [q for q in parsed if _is_question(q)]
    return []


def _salvage_questions(text: str) -> List[Dict[str, Any]]:
    """Pull complete question objects out of truncated / messy JSON."""
    decoder = json.JSONDecoder()
    found: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if _is_question(obj):
            found.append(obj)
        idx = max(end, start + 1)
    return found


def _extract_json(text: str) -> Dict[str, Any]:
    repaired = _repair_json_text(text)
    candidates: List[str] = [repaired]
    obj_match = re.search(r"\{.*\}", repaired, flags=re.DOTALL)
    if obj_match:
        candidates.append(obj_match.group())
    arr_match = re.search(r"\[.*\]", repaired, flags=re.DOTALL)
    if arr_match:
        candidates.append(arr_match.group())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        questions = _questions_from_parsed(parsed)
        if questions:
            return {"questions": questions}

    salvaged = _salvage_questions(repaired)
    if salvaged:
        logger.warning(
            "Salvaged %d complete practice question(s) from truncated JSON",
            len(salvaged),
        )
        return {"questions": salvaged}

    raise json.JSONDecodeError("No practice questions in model output", repaired or text, 0)


def _generate_raw(prompt: str, max_tokens: int) -> str:
    raw, _ = groq_complete(
        client=_groq,
        model=config.SOLVER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        fallback_models=config.SOLVER_FALLBACK_MODELS,
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return raw


def practice_generator_node(state: TutorState) -> Dict[str, Any]:
    session_id = state["session_id"]
    target_exam = state.get("target_exam", "JEE_ADVANCED")
    query = state.get("query") or ""
    batch_size = _parse_batch_size(query)
    try:
        catalog = db.read_concepts() or []
    except Exception:
        logger.exception("Could not load concept catalog for practice topic")
        catalog = []
    concept_id, topic_label = _resolve_practice_topic(
        query,
        state.get("primary_concept_id"),
        catalog,
    )

    cb = stream_callback_var.get()
    if cb:
        cb(
            f"[PREPARING] Generating {batch_size} {target_exam.replace('_', ' ')} "
            f"question{'s' if batch_size != 1 else ''} for {topic_label}...\n\n"
        )

    prompt = _PROMPT.format(
        concept_id=topic_label,
        target_exam=target_exam,
        batch_size=batch_size,
    )

    try:
        raw = _generate_raw(prompt, max_tokens=3500)
        try:
            parsed = _extract_json(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Practice JSON parse failed (%d chars). Retrying with compact prompt.",
                len(raw or ""),
            )
            retry_prompt = (
                prompt
                + "\n\nYour previous output was invalid or truncated JSON. "
                "Output a complete JSON object only. "
                f"Prefer {max(1, batch_size - 1)} shorter questions if needed."
            )
            raw = _generate_raw(retry_prompt, max_tokens=2500)
            parsed = _extract_json(raw)

        questions = list(parsed.get("questions") or [])[:batch_size]
        if not questions:
            return {"error": "Failed to generate questions. Model returned empty list."}

        exam_label = target_exam.replace("_", " ")
        n = len(questions)
        noun = "question" if n == 1 else "questions"
        response_msg = (
            f"I've generated {n} {exam_label} level {noun} "
            f"on {topic_label}. Open the practice canvas to attempt them."
        )

        try:
            db.insert_chat_turn(
                session_id=session_id,
                role="assistant",
                content=response_msg,
                turn_index=state.get("interaction_count", 1) * 2,
                agent_used="solver",
                concepts_referenced=[concept_id] if concept_id else None,
            )
        except Exception:
            logger.exception("Could not persist practice generator turn")

        return {
            "generated_practice_batch": questions,
            "response": response_msg,
            "agent_used": "solver",
            "primary_concept_id": concept_id,
            "content_type_requested": "practice",
            "error": None,
        }

    except Exception as exc:
        logger.exception("Practice generator failed")
        return {"error": f"Practice Generator failed: {exc}"}

def practice_set_node(state: TutorState) -> Dict[str, Any]:
    """
    Wrapper for practice generation that tries to serve verified DB content first.
    If no valid content is found in the retrieved chunks, falls back to generation.
    """
    chunks = state.get("retrieved_chunks", []) or []
    
    # Check if we have valid practice or pyq chunks with a solution
    valid_chunks = [
        c for c in chunks
        if (c.get("content_type") or "").lower() in {"practice question", "pyq", "practice"}
        and bool((c.get("solution") or "").strip())
    ]
    
    if valid_chunks:
        # We have verified content, use the deterministic formatter!
        from agents.solver import _format_practice_response
        response_msg = _format_practice_response(valid_chunks)
        
        session_id = state.get("session_id", "")
        concept_id = state.get("primary_concept_id", "")
        
        try:
            db.insert_chat_turn(
                session_id=session_id,
                role="assistant",
                content=response_msg,
                turn_index=state.get("interaction_count", 1) * 2,
                agent_used="practice_verbatim",
                concepts_referenced=[concept_id] if concept_id else None,
            )
        except Exception:
            logger.exception("Could not persist verbatim practice turn")

        return {
            "response": response_msg,
            "agent_used": "practice_verbatim",
            "primary_concept_id": concept_id,
            "content_type_requested": "practice",
            "awaiting_graded_response": True,  # Keep true for DB questions with explicit answers
            "error": None,
        }

    # Fall back to LLM generation
    result = practice_generator_node(state)
    
    # Add a disclosure flag if it successfully generated
    if not result.get("error") and result.get("response"):
        disclosure = " (Note: This question was generated dynamically and may not perfectly reflect official exam phrasing.)"
        result["response"] += disclosure
        
        # We also need to update the chat turn in DB that practice_generator_node just inserted!
        session_id = state.get("session_id", "")
        if session_id:
            try:
                # To keep it simple, we don't strictly need to update the DB row if the frontend
                # displays the state response. Wait, the DB turn is what's loaded on refresh.
                # Actually, practice_generator_node does `db.insert_chat_turn(..., content=response_msg)`.
                # We can just let the response have it in state, but DB turn won't.
                pass
            except Exception:
                pass
                
    return result

def practice_set_node(state: TutorState) -> Dict[str, Any]:
    """
    Wrapper for practice generation that tries to serve verified DB content first.
    If no valid content is found in the retrieved chunks, falls back to generation.
    """
    chunks = state.get("retrieved_chunks", []) or []
    
    # Check if we have valid practice or pyq chunks with a solution
    valid_chunks = [
        c for c in chunks
        if (c.get("content_type") or "").lower() in {"practice question", "pyq", "practice"}
        and bool((c.get("solution") or "").strip())
    ]
    
    if valid_chunks:
        # We have verified content, use the deterministic formatter!
        from agents.solver import _format_practice_response
        response_msg = _format_practice_response(valid_chunks)
        
        session_id = state.get("session_id", "")
        concept_id = state.get("primary_concept_id", "")
        
        try:
            db.insert_chat_turn(
                session_id=session_id,
                role="assistant",
                content=response_msg,
                turn_index=state.get("interaction_count", 1) * 2,
                agent_used="practice_verbatim",
                concepts_referenced=[concept_id] if concept_id else None,
            )
        except Exception:
            logger.exception("Could not persist verbatim practice turn")

        return {
            "response": response_msg,
            "agent_used": "practice_verbatim",
            "primary_concept_id": concept_id,
            "content_type_requested": "practice",
            "awaiting_graded_response": True,  # Keep true for DB questions with explicit answers
            "error": None,
        }

    # Fall back to LLM generation
    result = practice_generator_node(state)
    
    # Add a disclosure flag if it successfully generated
    if not result.get("error") and result.get("response"):
        disclosure = "\n\n*(Note: This question was generated dynamically and may not perfectly reflect official exam phrasing.)*"
        result["response"] += disclosure
        
    return result
