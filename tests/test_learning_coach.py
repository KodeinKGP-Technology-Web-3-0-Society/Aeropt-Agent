"""The Background Learning Coach.

Two properties matter more than anything the coach says:

  1. Doing nothing must be a first-class outcome, reported as success. A coach
     whose "no action needed" reads as a failure gets tuned until it always
     acts, and then students learn to ignore it.
  2. It must not be able to speak to the student or rewrite their proficiency.
     Between-session reasoning acts on records, not on a conversation, and a
     student cannot push back on something they never see.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.learning_coach import COACH_SCOPES, coach_user, run_learning_coach
from agents.runtime.loop import LoopResult
from agents.runtime.toolbelt import ToolContext, Toolbelt, registry


def _all_tools():
    import mcp_tools.compute_tools      # noqa: F401
    import mcp_tools.curriculum_tools   # noqa: F401
    import mcp_tools.learner_tools      # noqa: F401
    import mcp_tools.handoff            # noqa: F401

    return registry()


class CoachScopeTests(unittest.TestCase):
    def setUp(self):
        ctx = ToolContext(user_id="u1", session_id="s1")
        self.belt = Toolbelt(_all_tools().values(), ctx, allowed_scopes=COACH_SCOPES)

    def test_the_coach_cannot_talk_to_the_student(self):
        self.assertNotIn("consult_expert", self.belt)

    def test_the_coach_cannot_grade_an_attempt(self):
        """It is reading records. No attempt is happening for it to mark."""
        self.assertNotIn("record_attempt", self.belt)

    def test_the_coach_can_remember_and_schedule(self):
        self.assertIn("remember_about_learner", self.belt)
        self.assertIn("schedule_review", self.belt)

    def test_the_coach_can_read_everything(self):
        self.assertIn("get_learner_context", self.belt)
        self.assertIn("retrieve_content", self.belt)


class CoachOutcomeTests(unittest.TestCase):
    def _run(self, loop_result, gathered="No activity on record."):
        with patch("agents.learning_coach._gather_activity", return_value=gathered), patch(
            "agents.learning_coach.db"
        ) as mock_db, patch(
            "agents.learning_coach.run_agent_loop", return_value=loop_result
        ):
            mock_db.read_learner_memories.return_value = []
            return run_learning_coach("u1", trigger="test")

    def test_doing_nothing_is_a_successful_outcome(self):
        result = self._run(
            LoopResult(text="Steady progress, nothing needs attention.", stop_reason="answered")
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["acted"])
        self.assertEqual(result["actions"], [])
        self.assertIsNone(result["error"])

    def test_the_note_is_returned_for_the_record(self):
        result = self._run(LoopResult(text="Saw a sign-convention pattern.", stop_reason="answered"))
        self.assertIn("sign-convention", result["note"])

    def test_a_failed_run_never_raises(self):
        with patch(
            "agents.learning_coach.run_agent_loop", side_effect=RuntimeError("groq down")
        ), patch("agents.learning_coach.db") as mock_db:
            mock_db.read_learner_memories.return_value = []
            result = coach_user("u1")
        self.assertFalse(result["ok"])
        self.assertFalse(result["acted"])
        self.assertIn("groq down", result["error"])


class ActedReportingTests(unittest.TestCase):
    """`acted` must mean something changed, not that a tool was called."""

    def _run_with_steps(self, steps):
        from agents.runtime.trace import TraceStep

        def _fake_loop(**kwargs):
            trace = kwargs["trace"]
            for name, preview in steps:
                trace.add(
                    TraceStep(kind="tool", name=name, ok=True, result_preview=preview)
                )
            return LoopResult(text="done", stop_reason="answered")

        with patch("agents.learning_coach._gather_activity", return_value="x"), patch(
            "agents.learning_coach.db"
        ) as mock_db, patch("agents.learning_coach.run_agent_loop", side_effect=_fake_loop):
            mock_db.read_learner_memories.return_value = []
            return run_learning_coach("u1")

    def test_a_persisted_write_counts_as_acting(self):
        result = self._run_with_steps(
            [("remember_about_learner", {"saved": True, "fact": "x"})]
        )
        self.assertTrue(result["acted"])
        self.assertFalse(result["memory_unavailable"])

    def test_a_write_that_persisted_nothing_does_not_count(self):
        """Without migration 006 the write tools succeed but store nothing."""
        result = self._run_with_steps(
            [("remember_about_learner", {"saved": False, "note": "memory disabled"})]
        )
        self.assertFalse(result["acted"])
        self.assertTrue(result["memory_unavailable"])

    def test_a_persisted_review_counts_as_acting(self):
        result = self._run_with_steps([("schedule_review", {"scheduled": True})])
        self.assertTrue(result["acted"])

    def test_reads_alone_are_not_acting(self):
        result = self._run_with_steps([("get_learner_context", {"weak_concepts": []})])
        self.assertFalse(result["acted"])
        self.assertFalse(result["memory_unavailable"])


if __name__ == "__main__":
    unittest.main()
