"""Canonical LaTeX output rules for student-facing LLM prompts.

The frontend renders markdown with remark-math + KaTeX (single-dollar inline).
These rules must appear in every generation prompt (solver, socratic, quiz).
"""

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
"""
