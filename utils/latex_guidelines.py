"""Canonical LaTeX output rules for student-facing LLM prompts.

The frontend renders markdown with remark-math + KaTeX (single-dollar inline).
These rules must appear in every generation prompt (solver, socratic, quiz).
"""

from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger("jee_tutor.latex")

LATEX_OUTPUT_RULES = """\
── LaTeX (KaTeX) — REQUIRED ──────────────────────────────────────────────────
The UI renders math with KaTeX. Invalid delimiters show as broken/red text.
Follow these rules exactly:

1. Inline math: wrap in single dollars on the SAME line.
   Correct:   the force is $F = ma$ along the axis.
   Wrong:     the force is F = ma   (plain text, will not render)
   Wrong:     the force is \\(F = ma\\)   (do not use \\( \\))

2. Display / important equations: wrap in double dollars on their OWN lines.
   Correct:
   $$
   \\vec{F}_net = \\frac{dp}{dt}
   $$
   Wrong: $$\\vec{F}_net = dp/dt$$ squeezed into a prose sentence
   Wrong: \\[ ... \\]   (do not use \\[ \\])

3. One delimiter pair per expression. Never nest $ inside $.
   Wrong:  $F = $ma$$
   Wrong:  $$F = $ma$$$

4. Use real LaTeX macros, never unicode physics letters inside math:
   \\vec{F}, \\frac{a}{b}, \\sin\\theta, \\omega, \\tau, \\times, \\cdot, \\approx, \\le, \\ge
   Subscripts/superscripts: $F_net$, $r^2$, $a_1$
   Words inside math: $\\mathrm{net}$ not raw English inside $...$

5. Do NOT double-escape backslashes. Write \\frac not \\\\frac.
   Do NOT write the two-character sequence backslash-n as a formula separator
   (that breaks macros like \\nu and \\neq). Put each display equation in its
   own $$ block.

6. Do not wrap whole English sentences in $...$. Only the math tokens.

7. The retrieved chunks below come from an OCR/ingestion pipeline and can
   contain corrupted LaTeX — unbalanced braces, stray characters, a macro
   name that doesn't exist. If a formula in the chunks looks malformed, do
   NOT copy it verbatim. Silently reconstruct the correct standard form from
   physics principles instead. Never expose the corruption to the student and
   never guess at a broken macro — write the equation you know to be correct.
"""


# ── Chunk sanitization (retrieval → prompt) ────────────────────────────────
# Applied to raw DB chunk text (core_text / latex_formulae_*) BEFORE it is
# shown to an LLM as "ground truth" source material — in solver.py, socratic.py,
# and content_summarizer.py. This fixes the mechanical, unambiguous corruption
# patterns (unicode physics symbols, literal "\n" used as a separator instead
# of a real newline, double-escaped macros from a JSON round-trip) so the model
# reads clean input instead of inheriting — and then possibly amplifying —
# whatever formatting noise is in the knowledge base. It does NOT attempt to
# repair genuinely broken LaTeX (unbalanced braces, corrupted macros); rule 7
# above is what handles that case, since guessing at a fix in code risks being
# wrong in a way the model reconstructing from physics knowledge is not.

_UNICODE_MATH_MAP = {
    "ω": r"\omega", "Ω": r"\Omega", "α": r"\alpha", "β": r"\beta",
    "γ": r"\gamma", "Γ": r"\Gamma", "τ": r"\tau", "θ": r"\theta",
    "Σ": r"\sum", "π": r"\pi", "μ": r"\mu",
    "²": "^2", "³": "^3", "×": r"\times", "·": r"\cdot", "→": r"\to",
}

_N_LATEX_MACROS = (
    "nu", "nabla", "neq", "ne", "notin", "ni", "nless", "ngtr",
    "napprox", "ncong", "nmid", "nparallel", "nrightarrow", "nRightarrow",
    "nleftarrow", "nLeftarrow", "nexists", "nsubseteq", "nsupseteq",
)
_N_MACRO_RE = re.compile(r"\\(?:" + "|".join(_N_LATEX_MACROS) + r")(?![a-zA-Z])")


def _protect_and_replace_stray_n(text: str, replacement: str) -> str:
    """Replace literal backslash-n separators; keep \\nu / \\neq / etc. intact."""
    protected: List[str] = []

    def _protect(match: "re.Match[str]") -> str:
        protected.append(match.group(0))
        return f"\x00N{len(protected) - 1}\x00"

    out = _N_MACRO_RE.sub(_protect, text)
    out = out.replace("\\\\n", replacement).replace("\\n", replacement)
    for i, val in enumerate(protected):
        out = out.replace(f"\x00N{i}\x00", val)
    return out


def sanitize_chunk_text_for_prompt(text: str) -> str:
    """
    Clean a raw Supabase chunk field (core_text, latex_formulae_core/solution)
    before it goes into an LLM prompt as source material.

    Mechanical fixes only — unicode math symbols to macros, stray literal
    "\\n" separators to real newlines, collapsed double-escaped backslashes.
    Leaves prose and already-valid LaTeX untouched.
    """
    if not text:
        return text
    out = text
    for symbol, macro in _UNICODE_MATH_MAP.items():
        out = out.replace(symbol, macro)
    out = _protect_and_replace_stray_n(out, "\n")
    # Collapse a JSON-round-trip double escape (\\frac -> \frac) without
    # touching intentional double backslashes inside e.g. \\ line breaks in
    # matrix/array environments (rare in this corpus; not worth the edge case).
    prev = None
    while prev != out:
        prev = out
        out = re.sub(r"\\\\([a-zA-Z]+)", r"\\\1", out)
    return out


# ── Output safety net (LLM response → student) ─────────────────────────────
# Applied once, at the single choke point for all student-facing text
# (agents/solver.py::prepare_student_markdown). Catches the one failure mode
# that's cheap and unambiguous to fix after generation: an odd number of `$`
# delimiters (the model started inline math and never closed it, usually by
# truncating mid-formula). Anything more structurally broken (unbalanced
# braces, an invalid macro) is NOT guessed at here — KaTeX's throwOnError:false
# on the frontend already degrades that gracefully to inline error text
# instead of breaking the page; find_latex_issues() below exists so that class
# of problem is at least logged and visible, rather than silent.

def balance_dollar_delimiters(text: str) -> str:
    """
    Escape a trailing unpaired `$` so it can't swallow the rest of the
    response as a math span. Mirrors the frontend's balanceMathDelimiters
    (Aeropt-frontend/src/lib/markdownMath.ts) so a truncated/malformed
    response degrades the same way on both sides.
    """
    if not text or "$" not in text:
        return text
    chars = list(text)
    open_indexes: List[int] = []
    i = 0
    n = len(chars)
    while i < n:
        if chars[i] == "$" and i + 1 < n and chars[i + 1] == "$":
            i += 2
            continue
        if chars[i] == "$":
            if open_indexes:
                open_indexes.pop()
            else:
                open_indexes.append(i)
        i += 1
    for idx in open_indexes:
        chars[idx] = "\\$"
    return "".join(chars)


_MATH_SPAN_RE = re.compile(r"\$\$([\s\S]*?)\$\$|\$([^$\n]+)\$")


def find_latex_issues(text: str) -> List[str]:
    """
    Best-effort detector for malformed LaTeX in a finished response, for
    logging/telemetry — not a hard gate, since a false positive must never
    block a response from reaching the student. Returns a list of short
    issue descriptions (empty if nothing suspicious found).
    """
    if not text:
        return []
    issues: List[str] = []

    dollar_count = len(re.findall(r"(?<!\\)\$", text))
    if dollar_count % 2 != 0:
        issues.append("odd number of unescaped $ delimiters")

    for match in _MATH_SPAN_RE.finditer(text):
        span = match.group(1) if match.group(1) is not None else match.group(2)
        depth = 0
        for ch in span:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    issues.append(f"unbalanced braces in math span: {span[:60]!r}")
                    break
        else:
            if depth != 0:
                issues.append(f"unbalanced braces in math span: {span[:60]!r}")

    if "\\(" in text or "\\)" in text or "\\[" in text or "\\]" in text:
        issues.append("unconverted \\( \\) / \\[ \\] delimiter found")

    return issues

