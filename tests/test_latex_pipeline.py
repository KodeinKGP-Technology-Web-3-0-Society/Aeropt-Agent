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

    def test_sanitize_strips_nested_dollars(self) -> None:
        cleaned = _sanitize_latex_expr(r"$$\omega=240\pi$$")
        self.assertNotIn("$", cleaned)
        self.assertIn(r"\omega", cleaned)


if __name__ == "__main__":
    unittest.main()
