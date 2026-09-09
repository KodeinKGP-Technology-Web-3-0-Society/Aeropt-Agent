"""Practice generator JSON salvage — truncated / messy LLM dumps."""

from __future__ import annotations

import json
import unittest

from agents.practice_generator import (
    _extract_json,
    _parse_batch_size,
    _resolve_practice_topic,
)


Q1 = {
    "type": "mcq",
    "difficulty": "JEE Advanced",
    "question_text": "A block of mass $m$ slides.",
    "options": [
        {"id": "A", "latex": "$g$"},
        {"id": "B", "latex": "$2g$"},
        {"id": "C", "latex": "$g/2$"},
        {"id": "D", "latex": "$3g$"},
    ],
    "correct_answer": "C",
    "solution_walkthrough": "Use $a = g\\sin\\theta$.",
}

Q2 = {
    "type": "numerical",
    "difficulty": "JEE Advanced",
    "question_text": "Find the tension in N.",
    "correct_answer": "10",
    "solution_walkthrough": "T = mg.",
}


class TestExtractPracticeBatchJson(unittest.TestCase):
    def test_clean_wrapper(self) -> None:
        raw = json.dumps({"questions": [Q1, Q2]})
        parsed = _extract_json(raw)
        self.assertEqual(len(parsed["questions"]), 2)
        self.assertEqual(parsed["questions"][0]["correct_answer"], "C")

    def test_markdown_fence_and_think(self) -> None:
        raw = (
            "<think>planning the questions</think>\n"
            "```json\n"
            + json.dumps({"questions": [Q1]})
            + "\n```"
        )
        parsed = _extract_json(raw)
        self.assertEqual(parsed["questions"][0]["type"], "mcq")

    def test_truncated_unterminated_string_keeps_complete_questions(self) -> None:
        """Mirrors the production failure: max_tokens cut mid-string on Q3."""
        complete = json.dumps({"questions": [Q1, Q2]}, indent=2)
        truncated = complete.rstrip()[:-1] + (
            ',\n    {\n      "type": "mcq",\n'
            '      "question_text": "A rod of length $L$ is hinged at'
        )
        with self.assertRaises(json.JSONDecodeError):
            json.loads(truncated)
        parsed = _extract_json(truncated)
        self.assertEqual(len(parsed["questions"]), 2)
        self.assertEqual(parsed["questions"][1]["type"], "numerical")

    def test_latex_single_backslashes(self) -> None:
        raw = (
            '{"questions":[{"type":"mcq","question_text":"$\\\\vec{F}$",'
            '"correct_answer":"A","solution_walkthrough":"$F=ma$"}]}'
        )
        # Simulate model emitting \vec without doubling
        messy = raw.replace("\\\\vec", "\\vec")
        parsed = _extract_json(messy)
        self.assertIn("question_text", parsed["questions"][0])
        self.assertTrue(parsed["questions"][0]["question_text"].strip())


_CONCEPTS = [
    {
        "concept_id": "angular_kinematics",
        "concept_name": "Angular Kinematics",
        "chapter": "Rotational Motion",
    },
    {
        "concept_id": "torque",
        "concept_name": "Torque",
        "chapter": "Rotational Motion",
    },
]


class TestPracticeRequestParsing(unittest.TestCase):
    def test_one_rotational_motion_question_is_count_one(self) -> None:
        self.assertEqual(_parse_batch_size("1 rotational motion question"), 1)

    def test_a_question_is_one(self) -> None:
        self.assertEqual(_parse_batch_size("give me a practice question on torque"), 1)

    def test_three_questions(self) -> None:
        self.assertEqual(_parse_batch_size("generate 3 questions on friction"), 3)

    def test_bare_practice_defaults_to_three(self) -> None:
        self.assertEqual(_parse_batch_size("give me practice on laws of motion"), 3)

    def test_topic_rotational_motion_not_physics_concepts(self) -> None:
        cid, label = _resolve_practice_topic(
            "1 rotational motion question",
            primary_concept_id=None,
            concepts=_CONCEPTS,
        )
        self.assertEqual(cid, "angular_kinematics")
        self.assertEqual(label.lower(), "rotational motion")


if __name__ == "__main__":
    unittest.main()
