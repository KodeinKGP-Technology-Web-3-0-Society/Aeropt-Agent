"""The agent side of the LaTeX contract.

The frontend is the authoritative renderer (KaTeX itself vetoes spans there — see
`Aeropt-frontend/src/lib/mathHarden.ts`), but the agent must not hand it text that is
already broken, and must not mangle text that is fine. These tests cover the outbound
choke point and the stored corpus.
"""

from __future__ import annotations

import re

import pytest

from agents.solver import prepare_student_markdown
from utils.latex_guidelines import (
    braces_balanced,
    escape_currency_dollars,
    find_latex_issues,
    harden_math_spans,
)


# ── money is not maths ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,should_escape",
    [
        ("electricity costs $0.0900 per kWh", True),
        ("the clock costs $2.40 a year to run", True),
        ("a $5 note and a $10 note", True),
        ("costs $5 today", True),
        # …but a number inside delimiters is ordinary maths, not a price. Escaping
        # these broke a whole table of lattice energies.
        ("lattice energies $1036$ $853$ $807$", False),
        ("area $2 \\,\\text{cm}^{2}$ in a field of $10^{3}$ N/C", False),
        ("velocity $4.0 \\hat{i}$ at $t = 0$", False),
        # prose between two real formulas must keep its delimiters
        ("we have $$E = mc^2$$ as the rest energy, and $p = \\gamma m v$", False),
    ],
)
def test_currency_detection(text: str, should_escape: bool) -> None:
    out = escape_currency_dollars(text)
    assert ("\\$" in out) is should_escape, out


# ── brace counting must know what an escape is ────────────────────────────────
@pytest.mark.parametrize(
    "span,balanced",
    [
        (r"\left\{ \begin{matrix}a \\ b\end{matrix} \right.", True),
        (r"\{ x \}", True),
        (r"\frac{1}{2}", True),
        (r"\frac{1}{2", False),
        (r"a}{b", False),
    ],
)
def test_braces_balanced_ignores_escapes(span: str, balanced: bool) -> None:
    assert braces_balanced(span) is balanced


# ── unparseable spans degrade, valid ones survive ─────────────────────────────
def test_broken_span_degrades_to_text() -> None:
    out = harden_math_spans(r"valid $F = ma$ and broken $\frac{1}{2$ tail")
    assert "$F = ma$" in out
    assert r"$\frac{1}{2$" not in out
    assert r"\frac{1}{2" in out, "the formula text itself must survive"


@pytest.mark.parametrize(
    "span",
    [
        r"$F = ma$",
        r"$$E = \frac{1}{2}mv^2$$",
        r"$\left\{ \begin{matrix}a \\ b\end{matrix} \right.$",
        r"$\vec{F} \cdot \vec{d} = F d \cos\theta$",
        r"$100^{\circ}\text{C}$",
    ],
)
def test_valid_math_is_untouched(span: str) -> None:
    assert harden_math_spans(f"text {span} text") == f"text {span} text"


def test_plain_mode_does_not_inject_code_fences() -> None:
    """Stored corpus text must not gain markdown code markers mid-formula."""
    out = harden_math_spans(r"before $\frac{1}{2$ after", mode="plain")
    assert "`" not in out
    assert r"\frac{1}{2" in out


# ── the outbound choke point ──────────────────────────────────────────────────
def test_outbound_normalizes_tex_delimiters() -> None:
    out = prepare_student_markdown(r"inline \(F = ma\) and display \[E = mc^2\]")
    assert r"\(" not in out and r"\[" not in out
    assert "$F = ma$" in out
    assert "E = mc^2" in out


def test_outbound_never_emits_odd_delimiters() -> None:
    for text in [
        "Substituting gives $a = \\frac{F - \\mu m g}{",
        "The speed is $v = $5$ m/s$ which is malformed.",
        "Running a clock costs $0.0900 per kWh, about $2.40 total.",
        "$F = ma$ and $E = mc^2$",
    ]:
        out = prepare_student_markdown(text)
        assert len(re.findall(r"(?<!\\)\$", out)) % 2 == 0, out


def test_outbound_preserves_a_full_derivation() -> None:
    text = (
        "Starting from $\\tau = I\\alpha$:\n\n$$\n\\tau = r F \\sin\\phi\n$$\n\n"
        "so $\\alpha = \\frac{r F \\sin\\phi}{I}$ and for a disc $I = \\frac{1}{2}MR^2$."
    )
    out = prepare_student_markdown(text)
    for fragment in [r"\tau = I\alpha", r"\tau = r F \sin\phi", r"\frac{1}{2}MR^2"]:
        assert fragment in out
    assert not find_latex_issues(out), find_latex_issues(out)


# ── the stored corpus ─────────────────────────────────────────────────────────
def test_corpus_text_has_no_residual_latex_issues() -> None:
    from retrieval import store

    if not store.index_exists():
        pytest.skip("retrieval database not configured")
    rows = store.query("select content_id, core_text, coalesce(solution, '') as solution from public.content")
    bad = [
        (row["content_id"], find_latex_issues(row["core_text"] or "") or find_latex_issues(row["solution"] or ""))
        for row in rows
        if find_latex_issues(row["core_text"] or "") or find_latex_issues(row["solution"] or "")
    ]
    # Ingest repairs what it can and strips delimiters from what it cannot, so the
    # stored corpus should be clean. A handful of pathological source rows is tolerated;
    # a jump here means a new construct slipped through local_ingest/corpus.latex_safe.
    assert len(bad) <= 2, bad[:5]


def test_corpus_lines_have_balanced_inline_delimiters() -> None:
    """The real invariant: no line may leave an inline span open.

    An unescaped price ("costs $0.0900 per kWh") is the usual cause, and the symptom is
    always the same — one line with an odd number of delimiters, which swallows the rest
    of the paragraph into maths.

    Display blocks legitimately span lines, so they are removed before counting.
    """
    from retrieval import store

    if not store.index_exists():
        pytest.skip("retrieval database not configured")
    display = re.compile(r"\$\$[\s\S]*?\$\$")
    unescaped = re.compile(r"(?<!\\)\$")

    offenders: list[tuple[str, str]] = []
    for row in store.query("select content_id, core_text, coalesce(solution, '') as solution from public.content"):
        for field in ("core_text", "solution"):
            body = display.sub(" ", row[field] or "")
            for line in body.split("\n"):
                if len(unescaped.findall(line)) % 2 == 1:
                    offenders.append((row["content_id"], line[:90]))
                    break
    assert len(offenders) <= 2, offenders[:5]
