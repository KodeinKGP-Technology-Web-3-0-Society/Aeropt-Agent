"""Lexical concept/chapter matching for the concept resolver."""

from __future__ import annotations

import unittest

from mcp_tools.resolve_concept_id import infer_content_type, lexical_resolve


_CONCEPTS = [
    {"concept_id": "newtons_1st_law", "concept_name": "Newton's 1st Law", "chapter": "Laws of Motion"},
    {"concept_id": "newtons_2nd_law_f__ma", "concept_name": "Newton's 2nd Law (F = ma)", "chapter": "Laws of Motion"},
    {"concept_id": "static_friction", "concept_name": "Static Friction", "chapter": "Laws of Motion"},
    {"concept_id": "angular_kinematics", "concept_name": "Angular Kinematics", "chapter": "Rotational Motion"},
    {"concept_id": "torque", "concept_name": "Torque", "chapter": "Rotational Motion"},
    {"concept_id": "moment_of_inertia", "concept_name": "Moment of Inertia", "chapter": "Rotational Motion"},
    {"concept_id": "work_by_constant_force", "concept_name": "Work by a Constant Force", "chapter": "Work, Energy and Power"},
]


class TestLexicalResolve(unittest.TestCase):
    def test_learn_rotational_motion_is_in_scope(self):
        hit = lexical_resolve("I want to learn rotational motion", _CONCEPTS)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertTrue(hit["in_scope"])
        self.assertEqual(hit["primary_concept_id"], "angular_kinematics")
        self.assertEqual(hit["content_type_requested"], "theory")

    def test_banana_is_not_a_concept(self):
        self.assertIsNone(lexical_resolve("explain me what is banana", _CONCEPTS))

    def test_named_concept_wins_over_chapter(self):
        hit = lexical_resolve("Explain torque in rotational motion", _CONCEPTS)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["primary_concept_id"], "torque")

    def test_learn_implies_theory(self):
        self.assertEqual(infer_content_type("I want to learn rotational motion"), "theory")

    def test_practice_request_type(self):
        self.assertEqual(infer_content_type("Give me a practice question on friction"), "practice")


if __name__ == "__main__":
    unittest.main()
