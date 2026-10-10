"""Routing tests for the autonomous-agent graph.

There is far less to test here than there used to be, and that is the point.
The old graph had five routing functions branching on a seven-value intent enum;
those tests are gone with the code. What remains is one structural decision —
does this turn have a shape that needs no reasoning — plus the personalisation
heuristic, which still runs but now advises the agent instead of routing it.
"""

from __future__ import annotations

import unittest

from agents.planner import compute_personalization_strategy
from graph.workflow import is_structured_input, route_after_turn, route_from_bootstrap


class PersonalizationTests(unittest.TestCase):
    """The heuristic itself is unchanged — only what is done with its output."""

    def test_cold_start_is_direct(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": True, "proficiency_score": 0.0},
            prereq_states=[],
            learning_profile=None,
            content_type="theory",
            query="",
        )
        self.assertEqual(strategy["teaching_mode"], "direct")
        self.assertEqual(strategy["framing"], "cold_start")

    def test_weak_prereq_is_prereq_first(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": False, "proficiency_score": 0.3},
            prereq_states=[
                {"concept_id": "math_basics", "proficiency_score": 0.2, "hop_count": 1}
            ],
            learning_profile=None,
            content_type="theory",
            query="",
        )
        self.assertEqual(strategy["teaching_mode"], "prereq_first")
        self.assertEqual(strategy["prereq_to_teach_first"], "math_basics")

    def test_socratic_mode(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": False, "proficiency_score": 0.6},
            prereq_states=[
                {"concept_id": "math_basics", "proficiency_score": 0.9, "hop_count": 1}
            ],
            learning_profile=None,
            content_type="theory",
            query="",
        )
        self.assertEqual(strategy["teaching_mode"], "socratic")
        self.assertTrue(strategy["use_socratic"])

    def test_mastery_is_practice(self):
        strategy = compute_personalization_strategy(
            target_state={"is_cold_start": False, "proficiency_score": 0.8},
            prereq_states=[
                {"concept_id": "math_basics", "proficiency_score": 0.9, "hop_count": 1}
            ],
            learning_profile={"learning_velocity": "average"},
            content_type="theory",
            query="",
        )
        self.assertEqual(strategy["teaching_mode"], "practice")


class StructuralRoutingTests(unittest.TestCase):
    """The fast path must key off input *shape*, never off message content."""

    def test_graded_button_click_takes_the_fast_path(self):
        self.assertTrue(is_structured_input({"has_graded_outcome": True}))
        self.assertEqual(
            route_from_bootstrap({"has_graded_outcome": True}), "graded_fast_path"
        )

    def test_free_text_goes_to_the_agent(self):
        for query in (
            "explain torque",
            "why is my answer wrong?",
            "explain X and then quiz me on it",       # compound
            "what's the difference between X and Y",  # cross-concept
            "1",                                      # bare digit, no grading context
            "hi",
        ):
            with self.subTest(query=query):
                state = {"query": query}
                self.assertFalse(is_structured_input(state))
                self.assertEqual(route_from_bootstrap(state), "learning_agent")

    def test_a_failed_bootstrap_ends_the_turn(self):
        self.assertEqual(route_from_bootstrap({"error": "no such user"}), "end")

    def test_message_text_never_reaches_the_fast_path_test(self):
        """Guards the regression that would rebuild an intent classifier here.

        If someone makes is_structured_input inspect the query, these two states
        stop agreeing, and this test says so.
        """
        for query in ("correct", "incorrect", "1", "0", "yes", "skip"):
            with self.subTest(query=query):
                self.assertFalse(is_structured_input({"query": query}))


class PostTurnRoutingTests(unittest.TestCase):
    def test_summariser_runs_when_due(self):
        self.assertEqual(
            route_after_turn({"run_context_manager": True}), "context_manager"
        )

    def test_summariser_skipped_when_not_due(self):
        self.assertEqual(route_after_turn({"run_context_manager": False}), "end")

    def test_error_skips_the_summariser(self):
        self.assertEqual(
            route_after_turn({"run_context_manager": True, "error": "boom"}), "end"
        )


if __name__ == "__main__":
    unittest.main()
