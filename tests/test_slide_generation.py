"""Phase 5 — slide generation / truncation helpers.

Run:  python -m unittest tests.test_slide_generation -v
"""
from __future__ import annotations

import unittest

from utils.presentation import (
    _extract_takeaway,
    _split_into_bite_sized_chunks,
    _truncate,
    build_example_slides,
    build_theory_slides,
)


class TestSlideGeneration(unittest.TestCase):
    def test_truncate_does_not_cut_mid_frac(self) -> None:
        raw = r"Start " + (r"\frac{1}{2}" * 40) + " end"
        out = _truncate(raw, 80)
        self.assertFalse(out.rstrip("…").endswith("\\fra"))
        self.assertLessEqual(len(out), 82)

    def test_truncate_balances_dollars(self) -> None:
        raw = "Prose $$\\omega=2\\pi f " + ("x" * 200)
        out = _truncate(raw, 60)
        self.assertEqual(out.count("$$") % 2, 0)

    def test_bite_keeps_equation_separate(self) -> None:
        text = "Torque is a turning effect.\n\\vec{\\tau}=\\vec{r}\\times\\vec{F}\nIt matters in rotation."
        chunks = _split_into_bite_sized_chunks(text, max_len=300)
        self.assertTrue(any("\\vec{\\tau}" in c for c in chunks))
        eq = [c for c in chunks if "\\vec{\\tau}" in c][0]
        self.assertNotIn("Torque is a turning", eq)

    def test_takeaway_prefers_insight_heading(self) -> None:
        response = "## Idea\nBody\n\n## Takeaway\nRemember that torque needs a lever arm."
        tip = _extract_takeaway(
            response=response,
            core="ignored",
            formula_exprs=[],
            concept_label="Torque",
        )
        self.assertIn("lever arm", tip)

    def test_theory_deck_caps_at_8(self) -> None:
        long_core = "\n\n".join(
            [f"Paragraph {i}. This explains concept detail number {i}." for i in range(12)]
        )
        slides = build_theory_slides(
            {
                "primary_concept_id": "torque",
                "retrieved_chunks": [
                    {
                        "core_text": long_core,
                        "latex_formulae_core": r"\tau=rF\sin\theta\n\tau=I\alpha",
                    }
                ],
                "response": "## Takeaway\nTorque equals force times lever arm.",
            }
        )
        self.assertLessEqual(len(slides), 8)
        self.assertGreaterEqual(len(slides), 4)
        self.assertTrue(any(s["type"] == "summary" for s in slides))

    def test_example_deck_caps_at_6_with_diagram_fallback(self) -> None:
        slides = build_example_slides(
            {
                "primary_concept_id": "torque",
                "retrieved_chunks": [
                    {
                        "core_text": "A force F acts at distance r.",
                        "solution": "1. Write \tau=rF.\n2. Substitute values.\n3. Solve for \tau.",
                        "latex_formulae_solution": r"\tau=rF",
                        "diagram_description_core": "Rod pivoted at O with force at tip.",
                    }
                ],
                "response": "",
            }
        )
        self.assertLessEqual(len(slides), 6)
        diagram = next(s for s in slides if s["type"] == "diagram")
        self.assertIn("Rod pivoted", diagram["body_md"])


if __name__ == "__main__":
    unittest.main()
