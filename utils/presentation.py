"""Map tutor pipeline state → structured flashcards / grading UI for the frontend."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from agents.solver import (
    prepare_student_markdown,
    _image_urls,
    _select_core_image_chunk,
    _split_formula_exprs,
    _sanitize_latex_expr,
    _extract_md_image_urls,
    _split_formula_text,
)

logger = logging.getLogger("jee_tutor.presentation")

SUMMARIZER_CORE_LEN_TRIGGER = 1200
SUMMARIZER_DETERMINISTIC_SLIDE_TRIGGER = 4

_PICKER_TYPES = {"theory", "example"}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.:;])\s+")


def _card_front(item: Dict[str, Any]) -> str:
    title = (item.get("title") or item.get("concept_name") or "").strip()
    core = (item.get("core_text") or item.get("preview") or "").strip()
    if title:
        return _truncate(title, 120)
    if core:
        first_line = core.split("\n", 1)[0].strip()
        return _truncate(first_line, 160)
    ctype = (item.get("content_type") or "Study next").replace("_", " ").title()
    return ctype


def _truncate(text: str, limit: int = 280) -> str:
    """Trim text without slicing through TeX macros, braces, or math delimiters."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    cut = text[:limit]
    # If we landed mid-command (\fra…) or mid-delimiter, pull back.
    if cut.endswith("\\") or re.search(r"\\[a-zA-Z]*$", cut):
        cut = re.sub(r"\\[a-zA-Z]*$", "", cut).rstrip()
    # Unbalanced $ → drop trailing half-open math
    if cut.count("$$") % 2 == 1:
        cut = cut.rsplit("$$", 1)[0].rstrip()
    elif cut.count("$") % 2 == 1:
        cut = cut.rsplit("$", 1)[0].rstrip()
    # Unbalanced braces → step back to last balanced prefix
    depth = 0
    last_balanced = len(cut)
    for i, ch in enumerate(cut):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if depth == 0:
            last_balanced = i + 1
    if depth > 0:
        cut = cut[:last_balanced].rstrip()

    for sep in ("\n\n", ". ", "; ", ", ", " "):
        idx = cut.rfind(sep)
        if idx >= max(40, limit // 3):
            cut = cut[: idx + (0 if sep == " " else len(sep))].rstrip()
            break

    cut = cut.rstrip("…,. \t")
    return (cut + "…") if cut else text[: limit - 1].rstrip() + "…"


def _first_solution_step(solution: str) -> str:
    if not solution:
        return "Re-read the question and identify known quantities."
    for line in solution.splitlines():
        cleaned = line.strip().lstrip("0123456789.)-•* ").strip()
        if cleaned:
            return _truncate(cleaned, 200)
    return _truncate(solution, 200)


def _weakest_prereq(prereq_states: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not prereq_states:
        return None
    ranked = sorted(
        prereq_states,
        key=lambda p: (p.get("hop_count", 99), p.get("proficiency_score", 1.0)),
    )
    return ranked[0]


def _as_katex_block(text: str) -> str:
    """Wrap raw LaTeX-ish DB strings in $$ delimiters — one expression per block.

    Do not run `prepare_student_markdown` here: callers + FormulaSlide would
    otherwise triple-normalize. Frontend formula mode is residual-repair only.
    """
    text = (text or "").strip()
    if not text:
        return text

    # Already a single well-formed display block from an upstream pass.
    if text.startswith("$$") and text.rstrip().endswith("$$") and text.count("$$") == 2:
        inner = _sanitize_latex_expr(text.strip("$").strip())
        return f"$$\n{inner}\n$$" if inner else ""

    pieces = _split_formula_text(text)
    if not pieces:
        cleaned = _sanitize_latex_expr(text)
        return f"$$\n{cleaned}\n$$" if cleaned else ""

    blocks: List[str] = []
    for piece in pieces[:6]:
        cleaned = _sanitize_latex_expr(piece)
        if not cleaned:
            continue
        blocks.append(f"$$\n{cleaned}\n$$")
    return "\n\n".join(blocks)


def _split_into_bite_sized_chunks(text: str, max_len: int = 320) -> List[str]:
    """Split dense DB prose into short slide-sized chunks.

    Equations stay on their own slides; soft line breaks inside prose become
    spaces so we do not fuse soft-wrapped TeX mid-macro.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Packed formula DB strings first
    formula_like = _split_formula_text(text)
    if len(formula_like) > 1 and all(_is_equation_chunk(p) or "=" in p for p in formula_like):
        return formula_like

    # Line-wise: keep equation lines separate from surrounding prose
    line_chunks: List[str] = []
    prose_buf: List[str] = []

    def flush_prose() -> None:
        nonlocal prose_buf
        if not prose_buf:
            return
        joined = " ".join(prose_buf)
        prose_buf = []
        line_chunks.extend(_pack_prose_sentences(joined, max_len))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_prose()
            continue
        if _is_equation_chunk(line) or (
            "\\" in line and len(re.findall(r"[A-Za-z]{3,}", re.sub(r"\\[a-zA-Z]+", " ", line))) <= 2
        ):
            flush_prose()
            line_chunks.append(line)
        else:
            prose_buf.append(line)
    flush_prose()
    return line_chunks or _pack_prose_sentences(text, max_len)


def _pack_prose_sentences(text: str, max_len: int) -> List[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        return [text] if text.strip() else []
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        # Keep equation-like sentences atomic
        if _is_equation_chunk(sentence):
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence)
            continue
        if not current:
            current = sentence
        elif len(current) + len(sentence) + 1 <= max_len:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _is_equation_chunk(text: str) -> bool:
    s = text.strip()
    if not s or s.count("=") == 0:
        return False
    if len(s) > 180:
        return False
    # Prose sentences with an equals sign should stay as concept slides
    words = re.findall(r"[A-Za-z]{3,}", re.sub(r"\\[a-zA-Z]+", " ", s))
    if len(words) > 3:
        return False
    return bool(re.search(r"\\[a-zA-Z]+|[=_^]|\\frac|[ωατΣΓ²³]", s))


def build_mistake_brief(
    *,
    error_type: str,
    chunk: Optional[Dict[str, Any]],
    prereq_states: List[Dict[str, Any]],
    concept_id: str,
    recurring_gap_note: Optional[str] = None,
) -> str:
    concept_label = concept_id.replace("_", " ").title()
    core = (chunk or {}).get("core_text") or ""
    solution = (chunk or {}).get("solution") or ""
    formula_core = (chunk or {}).get("latex_formulae_core") or ""
    formula_solution = (chunk or {}).get("latex_formulae_solution") or ""
    weak = _weakest_prereq(prereq_states)
    weak_name = (weak or {}).get("concept_name") or (weak or {}).get("concept_id", "")

    recurring_extra = f"\n\n{recurring_gap_note}" if recurring_gap_note else ""

    if error_type == "conceptual":
        idea = _truncate(core.split("\n")[0] if core else f"Core idea for {concept_label}", 220)
        extra = f" Review **{weak_name}** first if that foundation feels shaky." if weak_name else ""
        return f"**Concept check:** {idea}{extra}{recurring_extra}"

    if error_type == "procedural":
        step = _first_solution_step(solution)
        return f"**Method check:** Verify your step order. Start here: {step}{recurring_extra}"

    if error_type == "calculation":
        formula = _truncate(formula_solution or formula_core or "Re-check units and algebra.", 180)
        return f"**Calculation check:** {_as_katex_block(formula)}{recurring_extra}"

    if error_type == "misread":
        return f"**Read carefully:** Restate what is given vs what you need to find.\n\n{_truncate(core, 240)}{recurring_extra}"

    return f"**Review {concept_label}:** Compare your approach with the worked solution and note where it diverged.{recurring_extra}"


def build_remediation_cards(
    *,
    chunk: Optional[Dict[str, Any]],
    error_type: str,
    prereq_states: List[Dict[str, Any]],
    concept_id: str,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    formula = (chunk or {}).get("latex_formulae_core") or (chunk or {}).get("latex_formulae_solution") or ""
    solution = (chunk or {}).get("solution") or ""
    weak = _weakest_prereq(prereq_states)

    chunk_id = (chunk or {}).get("content_id") or concept_id

    if formula:
        cards.append(
            {
                "id": f"remediation-{chunk_id}-formula",
                "card_type": "formula",
                "front": "Key relation for this topic",
                "back": _as_katex_block(formula),
                "concept_id": concept_id,
                "content_id": (chunk or {}).get("content_id"),
                "selectable": False,
            }
        )

    if solution:
        cards.append(
            {
                "id": f"remediation-{chunk_id}-step",
                "card_type": "micro_step",
                "front": "Next step to verify",
                "back": prepare_student_markdown(_first_solution_step(solution)),
                "concept_id": concept_id,
                "content_id": (chunk or {}).get("content_id"),
                "selectable": False,
            }
        )

    if weak:
        weak_name = weak.get("concept_name") or weak.get("concept_id", "prerequisite")
        weak_id = weak.get("concept_id") or "prereq"
        cards.append(
            {
                "id": f"remediation-{chunk_id}-prereq-{weak_id}",
                "card_type": "prereq",
                "front": "Foundation to review",
                "back": weak_name,
                "concept_id": weak.get("concept_id"),
                "selectable": True,
            }
        )

    if not cards:
        cards.append(
            {
                "id": f"remediation-{chunk_id}-review",
                "card_type": "theory",
                "front": f"Review {concept_id.replace('_', ' ')}",
                "back": build_mistake_brief(
                    error_type=error_type or "conceptual",
                    chunk=chunk,
                    prereq_states=prereq_states,
                    concept_id=concept_id,
                ),
                "concept_id": concept_id,
                "selectable": False,
            }
        )

    return cards[:4]


def filter_picker_suggestions(suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in suggestions or []:
        ctype = (item.get("content_type") or "").lower()
        normalized = ctype
        if "practice" in ctype or ctype == "pyq":
            continue
        if "example" in ctype:
            normalized = "example"
        elif "theory" in ctype:
            normalized = "theory"
        else:
            normalized = "theory"
        if normalized in _PICKER_TYPES:
            copy = dict(item)
            copy["content_type"] = normalized
            filtered.append(copy)
    return filtered[:3]


def build_picker_flashcards(suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for item in filter_picker_suggestions(suggestions):
        content_id = item.get("content_id")
        if not content_id:
            continue
        preview = _card_front(item)
        if len(preview) > 100:
            preview = _truncate(preview, 100)
        diff = item.get("difficulty")
        stars = "★" * int(diff) if diff else ""
        cards.append(
            {
                "id": content_id,
                "card_type": item.get("content_type", "theory"),
                "front": preview,
                "back": prepare_student_markdown(
                    _truncate(item.get("solution") or item.get("core_text") or preview, 320)
                ),
                "hint": stars or None,
                "concept_id": item.get("primary_concept_id"),
                "content_id": content_id,
                "selectable": True,
            }
        )
    return cards


def build_quiz_flashcards(
    quiz_steps: Optional[List[Dict[str, Any]]] = None,
    quiz_batch_chunks: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    if quiz_batch_chunks:
        for idx, chunk in enumerate(quiz_batch_chunks, start=1):
            cards.append(
                {
                    "id": chunk.get("content_id") or f"quiz-{idx}",
                    "card_type": "quiz",
                    "front": _truncate(chunk.get("core_text") or chunk.get("question") or f"Question {idx}", 400),
                    "back": prepare_student_markdown(
                        _truncate(chunk.get("solution") or chunk.get("answer") or "", 400)
                    ),
                    "hint": chunk.get("hint"),
                    "concept_id": chunk.get("primary_concept_id") or chunk.get("concept_id"),
                    "content_id": chunk.get("content_id"),
                    "selectable": False,
                }
            )
        return cards

    for step in quiz_steps or []:
        cards.append(
            {
                "id": f"quiz-step-{step.get('step_num', len(cards) + 1)}",
                "card_type": "quiz",
                "front": _truncate(step.get("question") or "", 400),
                "back": prepare_student_markdown(_truncate(step.get("answer") or "", 400)),
                "hint": step.get("hint"),
                "concept_id": step.get("concept_id"),
                "selectable": False,
            }
        )
    return cards


def build_grading_ui(awaiting: bool) -> Optional[Dict[str, Any]]:
    if not awaiting:
        return None
    return {
        "show_correct_incorrect": True,
        "prompt": "Attempt the question above, then tap Correct or Incorrect.",
    }


def build_quiz_assessment(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if result.get("quiz_batch_mode") and result.get("quiz_batch_chunks"):
        return {
            "batch_size": int(result.get("quiz_batch_size") or len(result.get("quiz_batch_chunks") or [])),
            "require_end_assessment": True,
        }
    if result.get("quiz_steps"):
        return {
            "batch_size": len(result.get("quiz_steps") or []),
            "require_end_assessment": True,
        }
    return None


def _proficiency_pct_from_state(state: Dict[str, Any]) -> int:
    if state.get("is_cold_start") or int(state.get("attempts") or 0) == 0:
        return 0
    return int(float(state.get("proficiency_score", 0.5)) * 100)


def build_proactive_card(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    node = result.get("sandbox_card")
    if isinstance(node, dict):
        return node
    return None


def build_flashcards_payload(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    remediation = result.get("remediation_flashcards")
    if remediation:
        return {"mode": "remediation", "cards": remediation}

    suggestions = result.get("follow_up_suggestions") or []
    picker = build_picker_flashcards(suggestions)
    if picker and (result.get("agent_used") in {"follow_up", "diagnostic"} or suggestions):
        return {"mode": "picker", "cards": picker}

    quiz_cards = build_quiz_flashcards(
        quiz_steps=result.get("quiz_steps"),
        quiz_batch_chunks=result.get("quiz_batch_chunks"),
    )
    if quiz_cards and (result.get("quiz_batch_mode") or result.get("quiz_steps")):
        return {"mode": "quiz", "cards": quiz_cards}

    return None


def split_score_summary(response: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not response or "Here is where you stand after this attempt" not in response:
        return response, None
    parts = response.split("**Here is where you stand after this attempt:**", 1)
    brief = parts[0].strip() or None
    summary = "**Here is where you stand after this attempt:**" + parts[1] if len(parts) > 1 else None
    return brief, summary


def _split_solution_steps(solution: str) -> List[str]:
    """
    Split a worked solution into coherent step slides.

    Prefers numbered/bulleted steps. Soft-wrapped lines (mid-sentence newlines)
    are rejoined so slides are never half a sentence.
    """
    raw = (solution or "").strip()
    if not raw:
        return []

    # Explicit numbered / lettered / bulleted steps
    parts = re.split(r"\n(?=\s*(?:\(?\d+[\.)]|[-•*]|\(?[a-z][\.)]|Step\s+\d+)\s+)", raw)
    if len(parts) >= 2:
        steps: List[str] = []
        for part in parts:
            cleaned = re.sub(
                r"^\s*(?:\(?\d+[\.)]|[-•*]|\(?[a-z][\.)]|Step\s+\d+:?)\s*",
                "",
                part.strip(),
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r"[ \t]*\n[ \t]*", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                steps.append(cleaned)
        return _merge_tiny_steps(steps)[:5]

    # Paragraphs separated by blank lines; soft-wrap joins within a paragraph
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    steps = []
    for para in paragraphs:
        cleaned = re.sub(r"[ \t]*\n[ \t]*", " ", para)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            steps.append(cleaned)

    if len(steps) <= 1 and len(raw) > 450:
        # Sentence-pack into ~2–4 readable slides (never mid-word / mid-clause)
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", re.sub(r"[ \t]*\n[ \t]*", " ", raw))
            if s.strip()
        ]
        packed: List[str] = []
        buf = ""
        for sentence in sentences:
            if not buf:
                buf = sentence
            elif len(buf) + len(sentence) + 1 <= 320:
                buf = f"{buf} {sentence}"
            else:
                packed.append(buf)
                buf = sentence
        if buf:
            packed.append(buf)
        steps = packed

    return _merge_tiny_steps(steps)[:5]


def _merge_tiny_steps(steps: List[str], min_chars: int = 48) -> List[str]:
    """Merge incomplete / tiny step fragments into the previous step."""
    if not steps:
        return []
    merged: List[str] = [steps[0]]
    for step in steps[1:]:
        prev = merged[-1]
        incomplete = not re.search(r"[.!?…:]$", prev.rstrip())
        if len(step) < min_chars or (incomplete and len(prev) < 220):
            merged[-1] = f"{prev} {step}".strip()
        else:
            merged.append(step)
    return merged


def _prepare_prose_markdown(text: str) -> str:
    """
    Normalize student-facing prose (problems / long solution text):
    keep paragraph structure, wrap only safe short math — never wrap sentences as math.
    """
    if not text:
        return text
    # Soft line wraps → spaces so renderers don't glue words oddly
    text = re.sub(r"(?<![.!?:;])\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *\n *", "\n\n", text)
    return prepare_student_markdown(text.strip())


def _split_markdown_sections(text: str) -> List[tuple[str, str]]:
    """Split markdown on ## headings into (title, body) pairs."""
    if not text or "##" not in text:
        return []
    parts = re.split(r"\n(?=##\s+)", text.strip())
    sections: List[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("##"):
            lines = part.split("\n", 1)
            title = lines[0].lstrip("#").strip()
            body = lines[1].strip() if len(lines) > 1 else ""
        else:
            title = "Overview"
            body = part
        if body:
            sections.append((title, body))
    return sections


def parse_structured_lesson(response: Optional[str]) -> Optional[Dict[str, Any]]:
    """Map markdown sections to legacy structured lesson fields.

    Returns None when nothing useful was found so clients do not render empty
    FILLING GAP / SOLUTION chrome.
    """
    if not response:
        return None

    sections = _split_markdown_sections(response)
    if not sections:
        return None

    filling_gap = ""
    solution_steps: List[str] = []
    insight = ""

    for title, body in sections:
        lower = title.lower()
        if "gap" in lower or "prereq" in lower or "foundation" in lower:
            filling_gap = body
        elif "solution" in lower or "step" in lower or "worked" in lower:
            solution_steps.extend(_split_solution_steps(body) or [body])
        elif "summary" in lower or "takeaway" in lower or "insight" in lower or "key" in lower:
            insight = body
        elif not filling_gap:
            filling_gap = body
        elif not insight:
            insight = body

    if not filling_gap and not solution_steps and not insight:
        return None

    return {
        "filling_gap": filling_gap,
        "solution_steps": solution_steps[:6],
        "insight": insight,
    }


def _strip_md_images(text: str) -> str:
    return re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text or "").strip()


def _extract_takeaway(
    *,
    response: str,
    core: str,
    formula_exprs: List[str],
    concept_label: str,
) -> str:
    """Prefer real takeaway sections; fall back to a short concept + formula tip."""
    for title, body in _split_markdown_sections(response):
        if any(k in title.lower() for k in ("summary", "takeaway", "key insight", "insight", "remember")):
            cleaned = _strip_md_images(body)
            if cleaned:
                return _truncate(cleaned, 420)
    structured = parse_structured_lesson(response)
    if structured and structured.get("insight"):
        return _truncate(_strip_md_images(structured["insight"]), 420)

    bites = _split_into_bite_sized_chunks(core, max_len=220) if core else []
    prose = next((b for b in bites if not _is_equation_chunk(b)), "")
    tip_parts: List[str] = []
    if prose:
        tip_parts.append(_truncate(prose, 220))
    if formula_exprs:
        tip_parts.append(f"Key relation: {_as_katex_block(formula_exprs[0])}")
    if tip_parts:
        return "\n\n".join(tip_parts)
    return f"Focus on the definition and main relation for **{concept_label}**."


def _estimate_deterministic_core_slide_count(
    core: str,
    *,
    para_cap: int,
    bite_cap: int,
) -> int:
    """How many core-text slides the deterministic chunker would emit (pre formula/diagram/summary)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", core) if p.strip()]
    if not paragraphs:
        paragraphs = [core]

    bite_chunks: List[str] = []
    for para in paragraphs[:para_cap]:
        bite_chunks.extend(_split_into_bite_sized_chunks(para, max_len=300))

    count = 0
    for piece in bite_chunks[:bite_cap]:
        if _is_equation_chunk(piece):
            count += len(_split_formula_text(piece) or [piece])
        else:
            count += 1
    return count


def _should_use_content_summarizer(
    core: str,
    *,
    para_cap: int,
    bite_cap: int,
) -> bool:
    if len(core) > SUMMARIZER_CORE_LEN_TRIGGER:
        return True
    return (
        _estimate_deterministic_core_slide_count(core, para_cap=para_cap, bite_cap=bite_cap)
        > SUMMARIZER_DETERMINISTIC_SLIDE_TRIGGER
    )


def _map_summarizer_cards_to_slides(
    cards: List[Dict[str, Any]],
    concept_id: str,
) -> List[Dict[str, Any]]:
    slides: List[Dict[str, Any]] = []
    for idx, card in enumerate(cards):
        card_type = (card.get("card_type") or "concept").strip().lower()
        title = (card.get("title") or "Concept").strip()
        body = (card.get("body") or "").strip()
        formula = (card.get("formula") or "").strip()

        if card_type == "takeaway":
            slide_type = "summary"
        elif card_type == "formula":
            slide_type = "formula"
        elif card_type == "example":
            slide_type = "concept"
        else:
            slide_type = "hook" if idx == 0 else "concept"

        parts: List[str] = []
        if body:
            parts.append(prepare_student_markdown(body))
        if formula:
            formula_block = _as_katex_block(formula)
            if formula_block and formula_block not in "\n\n".join(parts):
                parts.append(formula_block)
        body_md = "\n\n".join(parts) if parts else prepare_student_markdown(title)

        slides.append(
            {
                "id": f"{concept_id}-summary-card-{idx}",
                "type": slide_type,
                "title": title,
                "body_md": body_md,
                "image_url": None,
            }
        )
    return slides


def _append_diagram_slide_if_present(
    slides: List[Dict[str, Any]],
    *,
    concept_id: str,
    concept_label: str,
    chunk: Dict[str, Any],
    chunks: List[Dict[str, Any]],
    response: str,
) -> None:
    img_chunk = _select_core_image_chunk(chunks) if chunks else None
    core_url = _image_urls(img_chunk).get("core") if img_chunk else None
    if not core_url:
        md_urls = _extract_md_image_urls(response)
        core_url = md_urls[0] if md_urls else None
    if not core_url and chunk:
        core_url = _image_urls({**chunk, "has_image_core": True}).get("core")

    diagram_desc = ""
    if img_chunk:
        diagram_desc = (img_chunk.get("diagram_description_core") or "").strip()
    if not diagram_desc and chunk:
        diagram_desc = (chunk.get("diagram_description_core") or "").strip()

    if not core_url and not diagram_desc:
        return

    fallback_body = (
        diagram_desc
        or f"Visual model for **{concept_label}**. If the image fails to load, "
        f"sketch the setup from the description above."
    )
    slides.append(
        {
            "id": f"{concept_id}-diagram",
            "type": "diagram",
            "title": "Visual model",
            "body_md": prepare_student_markdown(fallback_body),
            "image_url": core_url,
        }
    )


def _build_theory_core_slides_deterministic(
    *,
    core: str,
    concept_id: str,
    concept_label: str,
    para_cap: int,
    bite_cap: int,
) -> List[Dict[str, Any]]:
    slides: List[Dict[str, Any]] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", core) if p.strip()]
    if not paragraphs:
        paragraphs = [core]

    bite_chunks: List[str] = []
    for para in paragraphs[:para_cap]:
        bite_chunks.extend(_split_into_bite_sized_chunks(para, max_len=300))

    for idx, piece in enumerate(bite_chunks[:bite_cap]):
        if _is_equation_chunk(piece):
            for j, expr in enumerate(_split_formula_text(piece) or [piece]):
                slides.append(
                    {
                        "id": f"{concept_id}-concept-{idx}-{j}",
                        "type": "formula",
                        "title": "Key relation",
                        "body_md": _as_katex_block(expr),
                        "image_url": None,
                    }
                )
        else:
            slide_type = "hook" if idx == 0 else "concept"
            title = f"Why {concept_label} matters" if idx == 0 else concept_label
            slides.append(
                {
                    "id": f"{concept_id}-concept-{idx}",
                    "type": slide_type,
                    "title": title,
                    "body_md": prepare_student_markdown(piece),
                    "image_url": None,
                }
            )
    return slides


def _try_build_theory_slides_via_summarizer(
    result: Dict[str, Any],
    *,
    chunk: Dict[str, Any],
    core: str,
    concept_id: str,
    concept_label: str,
    chunks: List[Dict[str, Any]],
    slide_cap: int,
    est_deterministic: int,
) -> Optional[List[Dict[str, Any]]]:
    from agents.content_summarizer import summarize_to_slide_cards

    latex_formulae = (chunk.get("latex_formulae_core") or "").strip()
    target_exam = (result.get("target_exam") or "JEE").replace("_", " ")

    logger.info(
        "[TheorySlides] Summarizer path for %s (core_len=%d, est_deterministic=%d)",
        concept_id,
        len(core),
        est_deterministic,
    )

    cards = summarize_to_slide_cards(
        core_text=core,
        latex_formulae_core=latex_formulae,
        concept_label=concept_label,
        target_exam=target_exam,
    )
    slides = _map_summarizer_cards_to_slides(cards, concept_id)
    _append_diagram_slide_if_present(
        slides,
        concept_id=concept_id,
        concept_label=concept_label,
        chunk=chunk,
        chunks=chunks,
        response=(result.get("response") or "").strip(),
    )
    logger.info(
        "[TheorySlides] Summarizer produced %d slide(s) for %s",
        len(slides),
        concept_id,
    )
    return slides[:slide_cap]


def build_theory_slides(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    chunks = result.get("retrieved_chunks") or []
    chunk = chunks[0] if chunks else {}
    concept_id = result.get("primary_concept_id") or "concept"
    concept_label = concept_id.replace("_", " ").title()
    slides: List[Dict[str, Any]] = []
    depth = (result.get("preferred_depth") or "standard").strip().lower()
    slide_cap = {"quick": 4, "standard": 8, "deep": 8}.get(depth, 8)
    para_cap = 2 if depth == "quick" else 4
    bite_cap = 3 if depth == "quick" else 6

    core = (chunk.get("core_text") or "").strip()
    response = (result.get("response") or "").strip()

    if core:
        est_deterministic = _estimate_deterministic_core_slide_count(
            core, para_cap=para_cap, bite_cap=bite_cap
        )
        if _should_use_content_summarizer(core, para_cap=para_cap, bite_cap=bite_cap):
            try:
                summarized = _try_build_theory_slides_via_summarizer(
                    result,
                    chunk=chunk,
                    core=core,
                    concept_id=concept_id,
                    concept_label=concept_label,
                    chunks=chunks,
                    slide_cap=slide_cap,
                    est_deterministic=est_deterministic,
                )
                if summarized:
                    return summarized
            except Exception as exc:
                logger.warning(
                    "[TheorySlides] Summarizer failed for %s, falling back to deterministic builder: %s",
                    concept_id,
                    exc,
                )
                logger.info("[TheorySlides] Deterministic fallback for %s", concept_id)
        else:
            logger.info(
                "[TheorySlides] Deterministic path for %s (core_len=%d, est_deterministic=%d)",
                concept_id,
                len(core),
                est_deterministic,
            )

        slides.extend(
            _build_theory_core_slides_deterministic(
                core=core,
                concept_id=concept_id,
                concept_label=concept_label,
                para_cap=para_cap,
                bite_cap=bite_cap,
            )
        )

    formula_exprs = _split_formula_exprs([chunk]) if chunk else []
    existing_bodies = {s.get("body_md", "") for s in slides}
    for idx, expr in enumerate(formula_exprs[:3]):
        body = _as_katex_block(expr)
        if body in existing_bodies:
            continue
        slides.append(
            {
                "id": f"{concept_id}-formula-{idx}",
                "type": "formula",
                "title": "Key formula",
                "body_md": body,
                "image_url": None,
            }
        )

    response = response or (result.get("response") or "").strip()
    img_chunk = _select_core_image_chunk(chunks) if chunks else None
    core_url = _image_urls(img_chunk).get("core") if img_chunk else None
    if not core_url:
        md_urls = _extract_md_image_urls(response)
        core_url = md_urls[0] if md_urls else None
    if not core_url and chunk:
        core_url = _image_urls({**chunk, "has_image_core": True}).get("core")

    diagram_desc = ""
    if img_chunk:
        diagram_desc = (img_chunk.get("diagram_description_core") or "").strip()
    if not diagram_desc and chunk:
        diagram_desc = (chunk.get("diagram_description_core") or "").strip()

    if core_url or diagram_desc:
        fallback_body = (
            diagram_desc
            or f"Visual model for **{concept_label}**. If the image fails to load, "
            f"sketch the setup from the description above."
        )
        slides.append(
            {
                "id": f"{concept_id}-diagram",
                "type": "diagram",
                "title": "Visual model",
                "body_md": prepare_student_markdown(fallback_body),
                "image_url": core_url,
            }
        )

    summary_body = _extract_takeaway(
        response=response,
        core=core,
        formula_exprs=formula_exprs,
        concept_label=concept_label,
    )
    if summary_body:
        slides.append(
            {
                "id": f"{concept_id}-summary",
                "type": "summary",
                "title": "Takeaway",
                "body_md": prepare_student_markdown(summary_body),
                "image_url": None,
            }
        )

    # Pad thin decks from response headings so theory usually has 4–8 slides
    if len(slides) < 4 and response and depth != "quick":
        for idx, (title, body) in enumerate(_split_markdown_sections(response)):
            cleaned = _strip_md_images(body)
            if not cleaned:
                continue
            if any(s.get("title") == title for s in slides):
                continue
            slides.append(
                {
                    "id": f"{concept_id}-section-{idx}",
                    "type": "concept",
                    "title": title[:80] or "Concept",
                    "body_md": prepare_student_markdown(_truncate(cleaned, 500)),
                    "image_url": None,
                }
            )
            if len(slides) >= 4:
                break

    if not slides and response:
        for idx, (title, body) in enumerate(
            _split_markdown_sections(response)[: max(2, slide_cap // 2)]
        ):
            slides.append(
                {
                    "id": f"{concept_id}-section-{idx}",
                    "type": "concept",
                    "title": title,
                    "body_md": prepare_student_markdown(body),
                    "image_url": None,
                }
            )

    return slides[:slide_cap]


def build_example_slides(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    chunks = result.get("retrieved_chunks") or []
    chunk = chunks[0] if chunks else {}
    concept_id = result.get("primary_concept_id") or "example"
    slides: List[Dict[str, Any]] = []
    depth = (result.get("preferred_depth") or "standard").strip().lower()
    step_cap = 2 if depth == "quick" else 4
    slide_cap = 4 if depth == "quick" else 6

    problem = (chunk.get("core_text") or "").strip()
    if problem:
        slides.append(
            {
                "id": f"{concept_id}-problem",
                "type": "problem",
                "title": "Problem",
                "body_md": _prepare_prose_markdown(_truncate(problem, 1600)),
                "image_url": None,
            }
        )

    solution = (chunk.get("solution") or "").strip()
    steps = _split_solution_steps(solution)
    for idx, step in enumerate(steps[:step_cap]):
        slides.append(
            {
                "id": f"{concept_id}-step-{idx}",
                "type": "step",
                "title": f"Step {idx + 1}",
                "body_md": _prepare_prose_markdown(step),
                "image_url": None,
            }
        )

    formula_exprs = _split_formula_text(
        (chunk.get("latex_formulae_solution") or chunk.get("latex_formulae_core") or "").strip()
    )
    formula_limit = 1 if depth == "quick" else 2
    for idx, expr in enumerate(formula_exprs[:formula_limit]):
        slides.append(
            {
                "id": f"{concept_id}-formula-{idx}",
                "type": "formula",
                "title": "Key formula used" if idx == 0 else "Relation",
                "body_md": _as_katex_block(expr),
                "image_url": None,
            }
        )

    sol_url = _image_urls(chunk).get("solution") or _image_urls(chunk).get("core")
    if not sol_url:
        md_urls = _extract_md_image_urls(result.get("response") or "")
        sol_url = md_urls[0] if md_urls else None
    diagram_desc = (
        (chunk.get("diagram_description_solution") or chunk.get("diagram_description_core") or "").strip()
    )
    if sol_url or diagram_desc:
        slides.append(
            {
                "id": f"{concept_id}-diagram",
                "type": "diagram",
                "title": "Solution diagram",
                "body_md": prepare_student_markdown(
                    diagram_desc
                    or "Follow the figure for this worked example. Sketch it yourself if the image is missing."
                ),
                "image_url": sol_url,
            }
        )

    if not slides:
        response = (result.get("response") or "").strip()
        for idx, (title, body) in enumerate(_split_markdown_sections(response)[: max(2, slide_cap // 2)]):
            slides.append(
                {
                    "id": f"{concept_id}-section-{idx}",
                    "type": "step" if idx > 0 else "problem",
                    "title": title,
                    "body_md": _prepare_prose_markdown(body),
                    "image_url": None,
                }
            )

    return slides[:slide_cap]


def build_slides_payload(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content_type = (result.get("content_type_requested") or "").lower()
    if content_type == "example":
        slides = build_example_slides(result)
        mode = "example"
    elif content_type == "theory":
        slides = build_theory_slides(result)
        mode = "theory"
    else:
        return None

    if not slides:
        return None
    return {"mode": mode, "slides": slides, "current_index": 0}


def build_chat_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    response = result.get("response")
    score_summary = result.get("score_summary")
    if score_summary:
        brief = response
    else:
        brief, score_summary = split_score_summary(response)

    flashcards = build_flashcards_payload(result)
    grading_ui = build_grading_ui(bool(result.get("awaiting_graded_response")))
    quiz_assessment = build_quiz_assessment(result)
    card = build_proactive_card(result)
    target = result.get("target_concept_state") or {}
    if result.get("persistence_status") == "committed" and isinstance(result.get("mastery_pct"), int):
        proficiency_pct = result.get("mastery_pct")
    elif result.get("persistence_status") == "committed" and isinstance(result.get("proficiency_pct"), int):
        proficiency_pct = result.get("proficiency_pct")
    else:
        proficiency_pct = _proficiency_pct_from_state(target) if target else None
    if proficiency_pct is None and result.get("persistence_status") == "committed":
        # Diagnostic / sandbox may stash a direct pct on the result
        raw_pct = result.get("proficiency_pct")
        if isinstance(raw_pct, (int, float)):
            proficiency_pct = int(round(raw_pct))
    slides = build_slides_payload(result)
    structured = parse_structured_lesson(brief or response)
    follow_ups = result.get("follow_up_suggestions") or None
    if follow_ups is not None and len(follow_ups) == 0:
        follow_ups = None
    quiz_steps = result.get("quiz_steps") or None
    if quiz_steps is not None and len(quiz_steps) == 0:
        quiz_steps = None
    elif isinstance(quiz_steps, list):
        quiz_steps = [
            {key: value for key, value in step.items() if key != "answer"}
            for step in quiz_steps
            if isinstance(step, dict)
        ]

    payload: Dict[str, Any] = {
        "response": brief or response,
        "score_summary": score_summary,
        "agent_used": result.get("agent_used"),
        "follow_up_suggestions": follow_ups,
        "quiz_steps": quiz_steps,
        "practice_batch": result.get("practice_payload") or result.get("generated_practice_batch") or result.get("practice_batch"),
        "awaiting_graded_response": bool(result.get("awaiting_graded_response")),
        "flashcards": flashcards,
        "grading_ui": grading_ui,
        "quiz_assessment": quiz_assessment,
        "card": card,
        "slides": slides,
        "structured": structured,
        "mistake_brief": result.get("mistake_brief"),
        "proficiency_pct": proficiency_pct,
        "grade_status": result.get("grade_status"),
        "evidence_source": result.get("evidence_source"),
        "persistence_status": result.get("persistence_status"),
        "recorded": result.get("recorded"),
        "error": result.get("error") if result.get("persistence_status") not in {"failed", "unknown", "not_attempted"} else None,
        "primary_concept_id": result.get("primary_concept_id"),
        "content_type_requested": result.get("content_type_requested"),
    }
    return payload
