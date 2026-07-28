"""
Content Summarizer Agent (Track B — Phase 4)

Turns long Supabase theory chunks into 3–4 structured slide cards via Groq LLM.
Standalone module — integration with presentation.py is Phase 5.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from groq import Groq

import config
from utils.llm import groq_complete

logger = logging.getLogger("jee_tutor.content_summarizer")

_groq = Groq(api_key=config.GROQ_API_KEY)

CARD_TYPES = frozenset({"concept", "formula", "example", "takeaway"})
MIN_INPUT_FOR_THREE_CARDS = 500
MIN_CARDS_LONG = 3
MAX_CARDS = 4
MIN_RESPONSE_CHARS = 40

_THINK_RE = re.compile(
    r"<think(?:ing)?>.*?</think(?:ing)?>|<think(?:ing)?>.*",
    flags=re.DOTALL | re.IGNORECASE,
)

_SYSTEM_PROMPT = """\
You are a JEE Physics content summarizer. Your job is to distill retrieved syllabus \
theory into exactly 3 or 4 slide cards for a student app.

Rules (strict):
1. Output ONLY facts present in the provided core_text and latex_formulae_core — \
no generic Wikipedia filler, no invented examples, no content outside the source.
2. Preserve LaTeX in body and formula fields using $...$ or $$...$$ delimiters. \
In JSON strings escape every backslash twice (e.g. write \\\\vec{{F}} for $\\vec{{F}}$).
3. Return a JSON array only — no markdown fences, no commentary.
4. Each card object must have:
   - title (string, short heading)
   - body (string, 1–3 sentences of syllabus-grounded prose; LaTeX allowed)
   - card_type (one of: concept, formula, example, takeaway)
   - formula (optional string — key equation when card_type is formula or concept)
   - diagram_hint (optional string — only if core_text mentions a diagram/setup)
5. Produce exactly 3 cards for medium source text, exactly 4 for long source text \
(>{min_chars} characters). Never return more than 4 cards.
6. card_type guidance:
   - concept: core definition or principle
   - formula: primary equation with brief context
   - example: worked idea or application mentioned in source
   - takeaway: exam-relevant summary or common trap from source
""".format(min_chars=MIN_INPUT_FOR_THREE_CARDS)

_USER_PROMPT = """\
Concept: {concept_label}
Target exam: {target_exam}

── Core text (only facts you may use) ──
{core_text}

── LaTeX formulae from chunk (preserve in cards) ──
{latex_formulae_core}

Return a JSON array of 3 or 4 slide cards following the schema.\
"""


def _clean_output(raw: str) -> str:
    return _THINK_RE.sub("", raw).strip()


def _loads_json_lenient(raw: str) -> Any:
    """Parse JSON, repairing single backslashes from LaTeX (e.g. \\vec)."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", raw)
        return json.loads(fixed)


def _extract_json_array(text: str) -> List[Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        return _loads_json_lenient(match.group())
    parsed = _loads_json_lenient(text)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("cards", "slide_cards", "slides"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
    raise ValueError("LLM response is not a JSON array of cards")


def _normalize_card(raw: Dict[str, Any], index: int) -> Dict[str, Any]:
    title = str(raw.get("title") or f"Card {index + 1}").strip()
    body = str(raw.get("body") or "").strip()
    if not body:
        raise ValueError(f"Card {index + 1} missing body")

    card_type = str(raw.get("card_type") or "concept").strip().lower()
    if card_type not in CARD_TYPES:
        raise ValueError(f"Card {index + 1} has invalid card_type: {card_type!r}")

    card: Dict[str, Any] = {
        "title": title,
        "body": body,
        "card_type": card_type,
    }

    formula = raw.get("formula")
    if formula is not None and str(formula).strip():
        card["formula"] = str(formula).strip()

    diagram_hint = raw.get("diagram_hint")
    if diagram_hint is not None and str(diagram_hint).strip():
        card["diagram_hint"] = str(diagram_hint).strip()

    return card


def _validate_cards(cards: List[Dict[str, Any]], *, source_len: int) -> List[Dict[str, Any]]:
    if not cards:
        raise ValueError("Summarizer returned no cards")

    if len(cards) > MAX_CARDS:
        logger.warning("[ContentSummarizer] Trimming %d cards to %d", len(cards), MAX_CARDS)
        cards = cards[:MAX_CARDS]

    if source_len > MIN_INPUT_FOR_THREE_CARDS and len(cards) < MIN_CARDS_LONG:
        raise ValueError(
            f"Expected at least {MIN_CARDS_LONG} cards for input length {source_len}, got {len(cards)}"
        )

    return cards


def summarize_to_slide_cards(
    core_text: str,
    latex_formulae_core: str,
    concept_label: str,
    target_exam: str = "JEE",
) -> List[Dict[str, Any]]:
    """
    Summarize theory chunk text into 3–4 structured slide cards.

    Returns list of dicts:
      {title, body, card_type, formula?, diagram_hint?}
    """
    core_text = (core_text or "").strip()
    latex_formulae_core = (latex_formulae_core or "").strip()
    concept_label = (concept_label or "Concept").strip()
    source_len = len(core_text)

    if not core_text:
        raise ValueError("core_text is required")

    card_target = MAX_CARDS if source_len > MIN_INPUT_FOR_THREE_CARDS else MIN_CARDS_LONG
    user_prompt = _USER_PROMPT.format(
        concept_label=concept_label,
        target_exam=target_exam,
        core_text=core_text,
        latex_formulae_core=latex_formulae_core or "(none provided)",
    )
    user_prompt += f"\n\nProduce exactly {card_target} cards."

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw, model_used = groq_complete(
        client=_groq,
        model=config.SOLVER_MODEL,
        messages=messages,
        fallback_models=config.SOLVER_FALLBACK_MODELS,
        min_chars=MIN_RESPONSE_CHARS,
        temperature=0.3,
        max_tokens=1800,
    )
    cleaned = _clean_output(raw)

    if len(cleaned) < MIN_RESPONSE_CHARS:
        logger.warning(
            "[ContentSummarizer] Empty response after think-block removal (model=%s); retrying fallback.",
            model_used,
        )
        raw, model_used = groq_complete(
            client=_groq,
            model=config.SOLVER_FALLBACK_MODELS[0],
            messages=messages,
            fallback_models=config.SOLVER_FALLBACK_MODELS[1:],
            min_chars=MIN_RESPONSE_CHARS,
            temperature=0.3,
            max_tokens=1800,
        )
        cleaned = _clean_output(raw)

    parsed = _extract_json_array(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Expected JSON array of cards")

    cards = [_normalize_card(item, i) for i, item in enumerate(parsed) if isinstance(item, dict)]
    cards = _validate_cards(cards, source_len=source_len)

    logger.info(
        "[ContentSummarizer] %d card(s) for %r via %s",
        len(cards),
        concept_label,
        model_used,
    )
    return cards


if __name__ == "__main__":
    SAMPLE_CORE = """
Newton's Second Law of Motion states that the rate of change of linear momentum of a body
is directly proportional to the external force applied on it and takes place in the direction
of the applied force. For a particle of constant mass m, this reduces to the familiar vector
relation $\\vec{F}_{\\text{net}} = m\\vec{a}$, where $\\vec{a}$ is the acceleration of the particle.

When several forces act on a body, we draw a free-body diagram (FBD), isolate the body, and
resolve forces along chosen axes. The net force along each axis equals mass times the
acceleration component along that axis: $\\sum F_x = ma_x$ and $\\sum F_y = ma_y$.

In JEE problems involving pulleys and connected blocks, students often forget that tension
acts along the string and that constraint relations link accelerations of different masses.
For a massless frictionless pulley with two blocks of masses $m_1$ and $m_2$, the magnitude
of acceleration is $a = \\frac{(m_2 - m_1)g}{m_1 + m_2}$ when $m_2 > m_1$ and the pulley
is fixed.

Always choose a sign convention before writing equations. A common trap is to mix directions
when writing $T - m_1 g = m_1 a$ and $m_2 g - T = m_2 a$ for an Atwood machine.
""".strip()

    SAMPLE_FORMULAE = (
        r"\vec{F}_{net} = m\vec{a}; \sum F_x = ma_x; "
        r"a = \frac{(m_2 - m_1)g}{m_1 + m_2}"
    )

    try:
        result = summarize_to_slide_cards(
            core_text=SAMPLE_CORE,
            latex_formulae_core=SAMPLE_FORMULAE,
            concept_label="Newton's Second Law",
            target_exam="JEE",
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\n{len(result)} card(s) returned")
    except Exception as exc:
        print(f"Smoke test failed: {exc}")
        raise
