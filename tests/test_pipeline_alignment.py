"""Routing and retrieval_mode tests for the restored 9-agent graph."""

from __future__ import annotations

import unittest

from agents.planner import compute_personalization_strategy
from graph.workflow import (
    route_after_diagnostic,
    route_after_orchestrator,
    route_to_responder,
    route_after_response,
)


class PersonalizationTests(unittest.TestCase):
    def test_cold_start_is_direct(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": True, "proficiency_score": 0.0},
            prereq_states=[],
            learning_profile=None,
            content_type="theory",
            query=""
        )
        self.assertEqual(strategy["teaching_mode"], "direct")
        self.assertEqual(strategy["framing"], "cold_start")

    def test_weak_prereq_is_prereq_first(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": False, "proficiency_score": 0.3},
            prereq_states=[{"concept_id": "math_basics", "proficiency_score": 0.2, "hop_count": 1}],
            learning_profile=None,
            content_type="theory",
            query=""
        )
        self.assertEqual(strategy["teaching_mode"], "prereq_first")
        self.assertEqual(strategy["prereq_to_teach_first"], "math_basics")

    def test_socratic_mode(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": False, "proficiency_score": 0.6},
            prereq_states=[{"concept_id": "math_basics", "proficiency_score": 0.9, "hop_count": 1}],
            learning_profile=None,
            content_type="theory",
            query=""
        )
        self.assertEqual(strategy["teaching_mode"], "socratic")
        self.assertTrue(strategy["use_socratic"])

    def test_mastery_is_practice(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": False, "proficiency_score": 0.8},
            prereq_states=[{"concept_id": "math_basics", "proficiency_score": 0.9, "hop_count": 1}],
            learning_profile={"learning_velocity": "average"},
            content_type="theory",
            query=""
        )
        self.assertEqual(strategy["teaching_mode"], "practice")


class OrchestratorRoutingTests(unittest.TestCase):
    def test_graded_response_goes_to_diagnostic(self):
        route = route_after_orchestrator({"intent": "graded_response", "has_graded_outcome": True})
        self.assertEqual(route, "diagnostic")

    def test_quiz_response_goes_to_diagnostic(self):
        route = route_after_orchestrator({"intent": "quiz_response", "has_graded_outcome": True})
        self.assertEqual(route, "diagnostic")

    def test_follow_up_response_goes_to_follow_up(self):
        route = route_after_orchestrator({"intent": "follow_up_response"})
        self.assertEqual(route, "follow_up")


class ResponderRoutingTests(unittest.TestCase):
    def test_socratic_goes_to_socratic(self):
        route = route_to_responder({"use_socratic": True})
        self.assertEqual(route, "socratic")
        
    def test_normal_goes_to_solver(self):
        route = route_to_responder({"use_socratic": False})
        self.assertEqual(route, "solver")

    def test_solver_practice_skips_follow_up_until_graded(self):
        route = route_after_response({"content_type_requested": "practice"})
        self.assertEqual(route, "end")

    def test_graded_goes_to_diagnostic(self):
        route = route_after_response({"has_graded_outcome": True})
        self.assertEqual(route, "diagnostic")


class DiagnosticRoutingTests(unittest.TestCase):
    def test_diagnostic_practice_goes_to_follow_up(self):
        route = route_after_diagnostic({"content_type_requested": "practice"})
        self.assertEqual(route, "follow_up")

    def test_diagnostic_triggers_quiz(self):
        route = route_after_diagnostic(
            {"content_type_requested": "practice", "trigger_quiz": True}
        )
        self.assertEqual(route, "quiz")

    def test_diagnostic_quiz_goes_to_follow_up(self):
        route = route_after_diagnostic({"content_type_requested": "quiz"})
        self.assertEqual(route, "follow_up")


if __name__ == "__main__":
    unittest.main()
