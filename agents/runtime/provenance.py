"""Claims about where content came from are checked before the student sees them.

Two failures this catches, both seen in real replies:

* An image the model made up: `![…](https://example.com/jee_2016_diagram.png)`.
  The only images a reply may carry are the tutor's own diagrams,
  `![caption](diagram:<hash>)` from `draw_diagram`. Anything else is removed.
* A question the model wrote itself, introduced as "a famous question from JEE
  Advanced 2016 (Paper 2)". A student treats that as an official PYQ. An exam and
  year named in the reply must match material retrieved for this turn (or already
  in the conversation); otherwise the label is softened and the student is told.

Deterministic, no model: the prompt already says not to do either, and this is
what holds when the model does it anyway.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger("jee_tutor.provenance")

_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_OWN_DIAGRAM = "diagram:"

# "JEE Advanced 2016", "JEE Adv. 2016 (Paper 2)", "JEE Main 2019", "IIT-JEE 2012"
_EXAM_CLAIM = re.compile(
    r"\b(?P<exam>JEE\s*[- ]?\s*(?:Advanced|Adv\.?|Main(?:s)?)|IIT\s*[- ]?\s*JEE)"
    r"[\s,(]*(?P<year>(?:19|20)\d\d)"
    r"(?:[\s,(]*(?:Paper|P)\s*[- ]?(?P<paper>[12]))?\)?",
    re.IGNORECASE,
)


def _is_drawn_diagram(url: str) -> bool:
    """`diagram:<hash>` that draw_diagram actually produced. The model also writes
    made-up ids like `diagram:jee_2016_p2_q`, which render as a broken image."""
    if not url.startswith(_OWN_DIAGRAM):
        return False
    from services.diagrams import SVG_DIR, _HASH_RE

    digest = url[len(_OWN_DIAGRAM):]
    return bool(_HASH_RE.match(digest)) and (SVG_DIR / f"{digest}.svg").is_file()


def strip_foreign_images(text: str) -> Tuple[str, int]:
    """Remove images that are not diagrams the tutor drew. Returns (text, removed)."""
    removed = 0

    def repl(m: "re.Match[str]") -> str:
        nonlocal removed
        if _is_drawn_diagram(m.group(2)):
            return m.group(0)
        removed += 1
        return ""

    out = _IMAGE.sub(repl, text or "")
    if removed:
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out, removed


# A picture the model typed into the reply instead of sending it to draw_diagram.
# The student would see the TikZ source, one code span per line.
_TIKZ_ENV = re.compile(r"\\begin\{(tikzpicture|circuitikz)\}.*?\\end\{\1\}", re.DOTALL)


def _widen_to_wrapper(text: str, start: int, end: int) -> Tuple[int, int]:
    """Include a ``` fence, $$ … $$ or \\[ … \\] that holds only the picture.

    Only when the wrapper is really open around it: "$$a$$ <picture> $$b$$" must
    keep both maths blocks intact.
    """
    before = text[:start].rstrip()
    rest = text[end:]
    after_at = end + len(rest) - len(rest.lstrip())
    after = rest.lstrip()
    fence = re.search(r"```[A-Za-z]*$", before)
    if fence and before.count("```") % 2 == 1 and after.startswith("```"):
        return fence.start(), after_at + 3
    if before.endswith("$$") and before.count("$$") % 2 == 1 and after.startswith("$$"):
        return len(before) - 2, after_at + 2
    if before.endswith("\\[") and after.startswith("\\]"):
        return len(before) - 2, after_at + 2
    return start, end


def render_inline_tikz(text: str) -> Tuple[str, int, int]:
    """Compile TikZ written straight into the reply, as draw_diagram would.

    Returns (text, drawn, dropped). A picture that does not compile is removed:
    raw TikZ in front of a student is worse than no picture.
    """
    if not text or "\\begin{" not in text:
        return text, 0, 0
    from services.diagrams import render_tikz

    parts: List[str] = []
    pos = drawn = dropped = 0
    for m in _TIKZ_ENV.finditer(text):
        start, end = _widen_to_wrapper(text, m.start(), m.end())
        start = max(start, pos)
        parts.append(text[pos:start])
        try:
            diagram_id = render_tikz(m.group(0))["id"]
        except Exception as exc:  # noqa: BLE001 - a missing picture beats a failed turn
            logger.info("inline TikZ did not compile, removed: %s", exc)
            dropped += 1
            parts.append("\n\n")
        else:
            drawn += 1
            parts.append(f"\n\n![Diagram](diagram:{diagram_id})\n\n")
        pos = end
    if not drawn and not dropped:
        return text, 0, 0
    parts.append(text[pos:])
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip(), drawn, dropped


def _is_advanced(exam: str) -> bool:
    return "adv" in exam.lower()


def _verified(claim: "re.Match[str]", sources: str) -> bool:
    # Only JEE Advanced papers are in the verified set (no JEE Main, no old IIT-JEE).
    if not _is_advanced(claim.group("exam")):
        return False
    label = f"JEE Advanced {claim.group('year')}"
    if claim.group("paper"):
        label += f" Paper {claim.group('paper')}"
    return label.lower() in sources


def _label(m: "re.Match[str]") -> str:
    """'JEE Adv. 2016 (Paper 2)' -> 'JEE Advanced 2016 Paper 2'."""
    exam = "JEE Advanced" if _is_advanced(m.group("exam")) else (
        "JEE Main" if "main" in m.group("exam").lower() else "IIT-JEE"
    )
    label = f"{exam} {m.group('year')}"
    return label + (f" Paper {m.group('paper')}" if m.group("paper") else "")


def _source_text(chunks: Iterable[Dict[str, Any]], history: Iterable[str]) -> str:
    parts: List[str] = []
    for c in chunks or []:
        for key in ("citation", "source_book", "source_ref"):
            if c.get(key):
                parts.append(str(c[key]))
    parts.extend(h for h in history or [] if h)
    # Normalise "JEE Advanced 2016 Paper 2 — Q7" and "JEE Adv 2016 P2" alike.
    text = " ".join(parts).lower()
    text = re.sub(r"jee\s*[- ]?\s*adv(?:anced|\.)?", "jee advanced", text)
    return re.sub(r"\bp\s*([12])\b", r"paper \1", text)


def check_exam_claims(
    text: str,
    chunks: Iterable[Dict[str, Any]],
    history: Iterable[str] = (),
) -> Tuple[str, List[str]]:
    """Soften exam/year labels nothing in this turn backs up, and say so once.

    Returns (text, the unverified labels as written)."""
    sources = _source_text(chunks, history)
    unverified: List[str] = []

    def repl(m: "re.Match[str]") -> str:
        if _verified(m, sources):
            return m.group(0)
        unverified.append(_label(m))
        level = "JEE Advanced" if _is_advanced(m.group("exam")) else "JEE"
        return f"{level}-level practice"

    out = _EXAM_CLAIM.sub(repl, text or "")
    if unverified:
        named = unverified[0]
        out = (
            f"> **Note:** \"{named}\" could not be matched to the official papers your tutor "
            f"has, so it is shown here as JEE-level practice, not as an official past-year "
            f"question. For the real ones, ask for a paper by year, for example \"JEE Advanced "
            f"2016 Paper 2 questions\".\n\n" + out
        )
    return out, unverified
