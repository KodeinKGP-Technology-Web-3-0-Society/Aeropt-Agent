"""Phase 4 golden fixtures - agent-side LaTeX pipeline.

Run:  python -m unittest tests.test_latex_pipeline -v
"""
from __future__ import annotations

import unittest

from agents.follow_up import _extract_chunk_formulae
from agents.solver import (
    _replace_stray_n,
    _sanitize_latex_expr,
    _split_formula_text,
    prepare_student_markdown,
)
from utils.presentation import _as_katex_block


class TestLatexPipeline(unittest.TestCase):
    def test_nu_macro_survives_stray_n_split(self) -> None:
        pieces = _split_formula_text(r"F=m\nu\nE=mc^2")
        joined = " ".join(pieces)
        self.assertIn(r"\nu", joined)
        self.assertNotIn(r"\nE", joined)
        self.assertTrue(any("E=mc^2" in p or "mc^2" in p for p in pieces))

    def test_follow_up_extract_preserves_nu(self) -> None:
        block = _extract_chunk_formulae(
            {"latex_formulae_core": r"F=m\nu\nE=mc^2", "latex_formulae_solution": ""}
        )
        self.assertIsNotNone(block)
        assert block is not None
        self.assertIn(r"\nu", block)
        self.assertIn("$$", block)
        self.assertNotIn(r"\nE", block)

    def test_replace_stray_n_keeps_neq(self) -> None:
        out = _replace_stray_n(r"a\neq b\nc=d", "\n")
        self.assertIn(r"\neq", out)
        self.assertIn("\n", out)

    def test_as_katex_block_idempotent_on_wrapped(self) -> None:
        once = _as_katex_block(r"\vec{\tau}=\vec{r}\times\vec{F}")
        twice = _as_katex_block(once)
        self.assertEqual(once.count("$$"), twice.count("$$"))
        self.assertIn(r"\vec{\tau}", twice)

    def test_prepare_student_markdown_wraps_bare_frac(self) -> None:
        out = prepare_student_markdown(r"I=\frac{1}{2}MR^2")
        self.assertIn("$$", out)
        self.assertIn(r"\frac{1}{2}", out)

    def test_prepare_student_markdown_leaves_english(self) -> None:
        prose = "Torque is the turning effect of a force about a point."
        out = prepare_student_markdown(prose)
        self.assertNotIn("$$", out)
        self.assertIn("Torque is the turning effect", out)

    def test_prepare_student_markdown_mixed_prose_equation(self) -> None:
        raw = (
            "Analyzing system from Non Inertial frame of reference of block M_1\n"
            r"The acceleration of block M_1, a_1 = \frac{F}{M_1+M_2+M_3}"
        )
        out = prepare_student_markdown(raw)
        self.assertIn("Analyzing system", out)
        self.assertIn(r"$a_1 = \frac{F}{M_1+M_2+M_3}$", out)
        self.assertNotIn("$$\nThe acceleration", out)

    def test_prepare_student_markdown_collapses_diagram_labels(self) -> None:
        raw = (
            r"mv_0 = Mv ...(i)\n(2) Net torque on the system about COM of rod is zero.\n"
            r"COM\nL\n2\nBefore collision\nm\nv_0\n\omega"
        )
        out = prepare_student_markdown(raw)
        self.assertIn("COM L 2 Before collision m v_0", out)
        self.assertNotIn("COM\nL\n2\nBefore", out)

    def test_prepare_student_markdown_does_not_glue_sentence_as_math(self) -> None:
        """
        Bug found 2026-09-11: a sentence with 2+ macros and few counted words
        ("Angular speed \\omega = 2\\pi f in SHM.") was wrongly treated as
        display math and wrapped whole — KaTeX then renders every word
        glued edge-to-edge with no spaces. Mirrored (and independently
        fixed) in Aeropt-frontend's markdownMath.ts.
        """
        raw = "Angular speed \\omega = 2\\pi f in SHM."
        out = prepare_student_markdown(raw)
        self.assertIn("Angular speed", out)
        self.assertNotIn("$$\nAngular speed", out)

    def test_sanitize_strips_nested_dollars(self) -> None:
        cleaned = _sanitize_latex_expr(r"$$\omega=240\pi$$")
        self.assertNotIn("$", cleaned)
        self.assertIn(r"\omega", cleaned)


if __name__ == "__main__":
    unittest.main()


class TestDisplayBlocksPassThrough(unittest.TestCase):
    """Lines inside $$ … $$ are already maths; the formatter must not re-wrap them."""

    def test_own_line_display_block_unchanged(self) -> None:
        raw = "Then\n\n$$\nx = \\frac{mR}{m+M}\n$$\n\nso."
        out = prepare_student_markdown(raw)
        self.assertEqual(out, raw)
        self.assertNotIn("```", out)

    def test_aligned_derivation_stays_one_block(self) -> None:
        raw = (
            "Sub:\n\n$$\n\\begin{aligned}\nm(R - x) + M(-x) &= 0 \\\\\n"
            "x &= \\frac{mR}{m + M}\n\\end{aligned}\n$$\n\nDone."
        )
        self.assertEqual(prepare_student_markdown(raw), raw)

    def test_bare_stored_equation_line_is_still_wrapped(self) -> None:
        out = prepare_student_markdown("Using\nI=\\frac{1}{2}MR^2\nthen")
        self.assertIn("$$\nI=\\frac{1}{2}MR^2\n$$", out)


class TestKatexEnvironmentsKept(unittest.TestCase):
    def test_cross_product_determinant_and_gathered_render_as_maths(self) -> None:
        from utils.latex_guidelines import harden_math_spans

        det = (
            "$$\n\\vec{v}\\times\\vec{B}=\\begin{vmatrix}\\hat{\\imath}&\\hat{\\jmath}&\\hat{k}"
            "\\\\V_0&0&0\\\\0&0&B\\end{vmatrix}\n$$"
        )
        gathered = "$$\n\\begin{gathered}a=b\\\\c=d\\end{gathered}\n$$"
        self.assertEqual(harden_math_spans(det), det)
        self.assertEqual(harden_math_spans(gathered), gathered)
