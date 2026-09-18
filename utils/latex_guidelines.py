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


_ESCAPED_RE = re.compile(r"\\[\\{}$&%#_]")


def strip_escapes(span: str) -> str:
    r"""Remove escaped characters so brace counting sees only real grouping braces.

    `\{`, `\}` and `\\` are delimiters and line breaks, not groups. Counting them
    as groups reported perfectly valid LaTeX — every `\left\{ … \right.` piecewise
    definition — as unbalanced, which is why the corpus looked broken in 54 places
    that in fact render.
    """
    return _ESCAPED_RE.sub("", span or "")


def braces_balanced(span: str) -> bool:
    depth = 0
    for ch in strip_escapes(span):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


# ── currency ($) vs math ($) ──────────────────────────────────────────────────
# "the cost of electricity is $0.0900 per kWh" — a real sentence from the corpus.
# Left alone that `$` opens a math span which swallows the prose until the next `$`,
# so the paragraph vanishes into a KaTeX error.
#
# Telling the two apart needs the *span*, not the character. A first attempt escaped
# any `$` followed by a digit, which broke 219 chunks containing perfectly good maths
# like "a square surface of area $2\,\text{cm}^{2}$" — the opening delimiter of a real
# span starts with a digit too. So: pair the delimiters first, and only escape a pair
# whose content is money (a bare amount, no macros, no operators) or a `$` that has no
# partner at all.

_MONEY_BODY_RE = re.compile(
    r"^\s*\d[\d,]*(?:\.\d+)?\s*"
    r"(?:per\s+\w+|each|a\s+\w+|an\s+\w+|billion|million|thousand|[kKmMbB])?\s*$"
)
_MATH_EVIDENCE_RE = re.compile(r"[\\^_={}]|\\[a-zA-Z]+")
# Money needs a positive cue, not merely the absence of maths.
_MONEY_CUE_RE = re.compile(
    r"\b(?:per|each|a\s+\w+|an\s+\w+|billion|million|thousand|dollars?|cents?)\b|\.\d{2}\b",
    re.IGNORECASE,
)
_PAIRED_SPAN_RE = re.compile(r"(?<!\\)\$([^$\n]{1,120}?)(?<!\\)\$")
_LONE_MONEY_RE = re.compile(r"(?<!\\)\$(?=\d)")


def escape_currency_dollars(text: str) -> str:
    """Escape `$` that introduces an amount of money rather than a math span."""
    if not text or "$" not in text:
        return text

    def repl(match: "re.Match[str]") -> str:
        body = match.group(1)
        if _MATH_EVIDENCE_RE.search(body):
            return match.group(0)  # real maths — leave the delimiters alone
        if _MONEY_CUE_RE.search(body) and _MONEY_BODY_RE.match(body):
            # "$5 per hour$" is not a span anyone wrote on purpose: two amounts in
            # one sentence got paired with each other. A bare number is NOT enough
            # evidence — "$1036$" in a table of lattice energies is ordinary maths,
            # and escaping those broke 21 spans of a perfectly good data table.
            return "\\$" + body + "\\$"
        # "a $5 note and a $10 note" pairs the two amounts into one "span" of prose.
        # No macro, no operator, several English words *and* it starts at an amount.
        # Without the leading-digit test this also matched the prose sitting between
        # two legitimate formulas and escaped their delimiters.
        if body.strip()[:1].isdigit() and len(re.findall(r"[A-Za-z]{2,}", body)) >= 2:
            return "\\$" + body + "\\$"
        return match.group(0)

    out = _PAIRED_SPAN_RE.sub(repl, text)

    # Whatever is left unpaired: a `$` directly before a number, with no closing
    # partner on its line, is money.
    lines = []
    for line in out.split("\n"):
        if line.count("$") - line.count("\\$") * 0 == 0:
            lines.append(line)
            continue
        unescaped = len(re.findall(r"(?<!\\)\$", line))
        if unescaped % 2 == 1:
            line = _LONE_MONEY_RE.sub("\\$", line, count=1)
        lines.append(line)
    return "\n".join(lines)


# ── last-resort degradation ───────────────────────────────────────────────────
# KaTeX with throwOnError:false shows red error text rather than breaking the
# page, which protects the app but not the student: they see a red blob where the
# formula should be. A span that cannot parse is better shown as plain monospace
# text — still readable, still the real formula, and never visibly broken.

_UNPARSEABLE_MACRO_RE = re.compile(r"\\(?:begin|end)\{(\w+)\}")
_BALANCED_ENVIRONMENTS = ("matrix", "pmatrix", "bmatrix", "cases", "aligned", "array", "split")


def _span_is_safe(span: str) -> bool:
    body = (span or "").strip()
    if not body:
        return False
    if not braces_balanced(body):
        return False
    begins = re.findall(r"\\begin\{(\w+)\}", body)
    ends = re.findall(r"\\end\{(\w+)\}", body)
    if sorted(begins) != sorted(ends):
        return False
    if any(env not in _BALANCED_ENVIRONMENTS for env in begins):
        return False
    # An odd number of \left without \right (or vice versa) aborts the parse.
    if len(re.findall(r"\\left\b", body)) != len(re.findall(r"\\right\b", body)):
        return False
    # A dangling backslash is a control sequence with no name — KaTeX stops there.
    if re.search(r"(?<!\\)\\\s*$", body) or re.fullmatch(r"\s*\\+\s*", body):
        return False
    return True


def harden_math_spans(text: str, mode: str = "code") -> str:
    """Downgrade any math span that will not parse into plain text.

    `mode="code"` (outbound to the student) marks the fragment as code, which reads
    clearly in chat. `mode="plain"` (ingest) only removes the delimiters: inserting
    fences into stored prose interleaves code markers with the surrounding maths and
    makes the chunk worse than the defect it was fixing.

    This is a structural check, not a parse. The authoritative check runs in the
    frontend (`src/lib/mathHarden.ts`), where KaTeX itself is the oracle.
    """
    if not text or "$" not in text:
        return text
    plain = mode == "plain"

    def repl_display(match: "re.Match[str]") -> str:
        body = match.group(1)
        if _span_is_safe(body):
            return match.group(0)
        logger.warning("LaTeX degraded to text (display): %r", body[:80])
        return body.strip() if plain else "\n```\n" + body.strip() + "\n```\n"

    def repl_inline(match: "re.Match[str]") -> str:
        body = match.group(1)
        if _span_is_safe(body):
            return match.group(0)
        logger.warning("LaTeX degraded to text (inline): %r", body[:80])
        return body.strip() if plain else "`" + body.strip() + "`"

    out = re.sub(r"\$\$(.+?)\$\$", repl_display, text, flags=re.DOTALL)
    out = re.sub(r"(?<![\\$])\$([^$\n]+?)\$(?!\$)", repl_inline, out)
    return out


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
        if not braces_balanced(span):
            issues.append(f"unbalanced braces in math span: {span[:60]!r}")

    if "\\(" in text or "\\)" in text or "\\[" in text or "\\]" in text:
        issues.append("unconverted \\( \\) / \\[ \\] delimiter found")

    return issues

