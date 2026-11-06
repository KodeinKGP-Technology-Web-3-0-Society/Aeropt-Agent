"""Single release gate: every student-facing text passes through this before display.

Moves provenance checks (inline TikZ, foreign images, unverified exam claims)
and LaTeX hardening (prepare_student_markdown) into one deterministic function
called on whatever text is final, after the Answer Checker.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from agents.runtime.provenance import (
    check_exam_claims,
    render_inline_tikz,
    strip_foreign_images,
    strip_text_sketches,
)
from agents.solver import prepare_student_markdown

logger = logging.getLogger("jee_tutor.release")


def release(text: str, ctx: Optional[Any] = None) -> str:
    """Filter, sanitize, and harden student-facing text after all reasoning and checks."""
    if not text:
        return text or ""

    state: Dict[str, Any] = {}
    trace = None
    chunks: List[Dict[str, Any]] = []
    history: List[str] = []

    if ctx is not None:
        if hasattr(ctx, "state") and isinstance(ctx.state, dict):
            state = ctx.state
        elif isinstance(ctx, dict):
            state = ctx

        trace = getattr(ctx, "trace", None)
        if trace is None and isinstance(ctx, dict):
            trace = ctx.get("trace")

        updates = getattr(ctx, "updates", {}) if hasattr(ctx, "updates") else {}
        chunks = list(updates.get("retrieved_chunks") or []) + list(state.get("retrieved_chunks") or [])
        for passage in state.get("web_passages") or []:
            if isinstance(passage, dict) and passage.get("url"):
                chunks.append(
                    {
                        "citation": passage.get("url"),
                        "source_book": passage.get("title") or "official web",
                        "source_ref": (passage.get("text") or "")[:400],
                    }
                )
        history = [t.get("content") or "" for t in (state.get("recent_turns") or [])]

    # 1. Inline TikZ compilation, then throw away character-art sketches.
    out, tikz_drawn, tikz_dropped = render_inline_tikz(text)
    out = strip_text_sketches(out)
    out = _attach_diagram_if_missing(out, state)
    if trace and (tikz_drawn or tikz_dropped):
        trace.note(f"provenance: inline TikZ drawn {tikz_drawn}, removed {tikz_dropped}")

    # 2. Foreign images (only diagrams drawn by the agent are kept)
    out, dropped = strip_foreign_images(out)

    # 3. Exam claims (soften unverified PYQ assertions)
    out, unverified = check_exam_claims(out, chunks, history)
    if trace and (dropped or unverified):
        trace.note(f"provenance: dropped {dropped} image(s); unverified exam claims {unverified}")

    # 4. LaTeX hardening (normalize display math, currency signs, KaTeX compatibility)
    out = prepare_student_markdown(out)

    return out


_WANTS_PICTURE = re.compile(r"\b(diagram|picture|sketch|draw|figure)\b", re.IGNORECASE)


def _attach_diagram_if_missing(text: str, state: Dict[str, Any]) -> str:
    """If the student asked for a picture and the reply has none, draw one.

    The tutor is told to call draw_diagram. When it sketches with characters
    instead, that sketch has already been removed and this supplies a real SVG.
    """
    query = str(state.get("query") or "")
    if "diagram:" in text or not _WANTS_PICTURE.search(query):
        return text
    from services.diagrams import available, render_tikz

    if not available():
        return text
    from utils.llm import complete_text
    import config

    try:
        src, _ = complete_text(
            client=None,
            model=config.LEARNING_AGENT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You draw one physics, chemistry, or mathematics figure. "
                        "Reply with only a \\begin{tikzpicture}...\\end{tikzpicture} block. "
                        "Black lines, about 7cm wide. Label the symbols from the question in $...$. "
                        "No prose and no code fence."
                    ),
                },
                {"role": "user", "content": query[:1800]},
            ],
            temperature=0.2,
            max_tokens=900,
            min_chars=40,
            lane="background",
        )
        diagram_id = render_tikz(src)["id"]
    except Exception:
        logger.warning("could not supply a missing diagram", exc_info=True)
        return text
    image = f"![Diagram](diagram:{diagram_id})"
    parts = text.split("\n\n", 1)
    if len(parts) == 1:
        return f"{text}\n\n{image}".strip()
    return f"{parts[0]}\n\n{image}\n\n{parts[1]}".strip()
