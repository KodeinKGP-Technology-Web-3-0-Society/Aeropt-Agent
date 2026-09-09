"""Practice JSON extraction from solver dumps."""

from __future__ import annotations

import unittest

from agents.solver import extract_practice_payload, _fence_practice


class TestExtractPracticePayload(unittest.TestCase):
    def test_fenced_roundtrip_fields(self):
        raw = """
        Here you go
        {
          "type": "mcq",
          "difficulty": "JEE_ADVANCED",
          "question_text": "A rod of mass $m$.",
          "options": [{"id": "A", "latex": "3g/2L"}],
          "correct_answer": "A",
          "solution_walkthrough": "1. Torque\\n$$\\\\tau = I\\\\alpha$$"
        }
        extra
        """
        parsed = extract_practice_payload(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["type"], "mcq")
        self.assertIn("$m$", parsed["question_text"])
        fenced = _fence_practice(parsed)
        self.assertTrue(fenced.startswith("```interactive-practice"))
        self.assertIn('"type": "mcq"', fenced)

    def test_rejects_theory_prose(self):
        self.assertIsNone(extract_practice_payload("Torque is r cross F."))


if __name__ == "__main__":
    unittest.main()
