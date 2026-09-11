"""Expert subagents and the Answer Checker.

The expert tests assert the containment properties by construction rather than
by prompt: an expert cannot write memory, cannot schedule, cannot grade, and
cannot spawn another expert. The checker tests assert it improves answers when
it can and gets out of the way when it cannot.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.answer_checker import (
    VERDICT_PASS,
    VERDICT_REVISED,
    VERDICT_SKIPPED,
    check_answer,
)
from agents.experts import EXPERTS, draft_preview, expert_catalogue, run_expert
from agents.runtime.toolbelt import (
    EXPERT_SCOPES,
    TUTOR_SCOPES,
    ToolContext,
    Toolbelt,
    registry,
)


def _all_tools():
    import mcp_tools.compute_tools      # noqa: F401
    import mcp_tools.curriculum_tools   # noqa: F401
    import mcp_tools.learner_tools      # noqa: F401
    import mcp_tools.handoff            # noqa: F401

    return registry()


class ExpertContainmentTests(unittest.TestCase):
    """What an expert physically cannot do, regardless of what it is told."""

    def setUp(self):
        self.specs = _all_tools().values()
        self.ctx = ToolContext(user_id="u1", session_id="s1", depth=1)

    def _expert_belt(self):
        return Toolbelt(self.specs, self.ctx, allowed_scopes=EXPERT_SCOPES)

    def test_an_expert_cannot_write_learner_memory(self):
        self.assertNotIn("remember_about_learner", self._expert_belt())

    def test_an_expert_cannot_change_progress(self):
        self.assertNotIn("record_attempt", self._expert_belt())

    def test_an_expert_cannot_schedule_a_review(self):
        self.assertNotIn("schedule_review", self._expert_belt())

    def test_an_expert_cannot_delegate_further(self):
        self.assertNotIn("consult_expert", self._expert_belt())

    def test_an_expert_can_still_look_things_up(self):
        belt = self._expert_belt()
        for name in ("calculate", "retrieve_content", "get_learner_context"):
            with self.subTest(tool=name):
                self.assertIn(name, belt)

    def test_the_tutor_has_everything_the_expert_has_and_more(self):
        tutor = set(Toolbelt(self.specs, self.ctx, allowed_scopes=TUTOR_SCOPES).names)
        expert = set(self._expert_belt().names)
        self.assertTrue(expert < tutor)

    def test_depth_blocks_delegation_even_if_the_tool_is_reachable(self):
        """Belt-and-braces: scope withholds it, and the tool itself refuses."""
        from mcp_tools.handoff import ConsultExpertArgs, consult_expert

        result = consult_expert(
            ConsultExpertArgs(expert="verifier", task="check this calculation please"),
            self.ctx,
        )
        self.assertIn("cannot delegate further", result.get("error", ""))


class ExpertDispatchTests(unittest.TestCase):
    def setUp(self):
        self.ctx = ToolContext(user_id="u1", session_id="s1")

    def test_an_unknown_expert_reports_the_real_options(self):
        result = run_expert("nonexistent", "do a thing please", self.ctx)
        self.assertIn("verifier", result["error"])

    def test_an_expert_that_raises_does_not_end_the_turn(self):
        with patch("agents.solver.solver_node", side_effect=RuntimeError("solver down")):
            result = run_expert("worked_solution", "explain torque to me", self.ctx)
        self.assertIn("error", result)
        self.assertIn("Carry on without it", result["error"])

    def test_a_composer_draft_is_staged_for_verbatim_use(self):
        draft = "Here is a full worked solution with $F = ma$ and a figure."
        with patch(
            "agents.solver.solver_node",
            return_value={"response": draft, "model_used": "m"},
        ):
            result = run_expert("worked_solution", "explain torque to me", self.ctx)
        self.assertEqual(self.ctx.updates["draft_response"], draft)
        self.assertEqual(self.ctx.updates["draft_author"], "worked_solution")
        self.assertIn("USE_DRAFT", result["next_step"])

    def test_an_empty_draft_is_reported_rather_than_sent(self):
        with patch("agents.solver.solver_node", return_value={"response": "   "}):
            result = run_expert("worked_solution", "explain torque to me", self.ctx)
        self.assertIn("error", result)
        self.assertNotIn("draft_response", self.ctx.updates)

    def test_handoffs_are_traced(self):
        with patch(
            "agents.solver.solver_node", return_value={"response": "x" * 50}
        ):
            run_expert("worked_solution", "explain torque to me", self.ctx)
        self.assertEqual(self.ctx.trace.handoffs, 1)
        self.assertEqual(self.ctx.trace.path(), ["handoff:worked_solution"])

    def test_the_catalogue_separates_composers_from_analysts(self):
        catalogue = expert_catalogue()
        self.assertIn("Composers", catalogue)
        self.assertIn("Analysts", catalogue)
        for name in EXPERTS:
            self.assertIn(name, catalogue)


class DraftPreviewTests(unittest.TestCase):
    def test_a_short_draft_is_shown_whole(self):
        draft = "A short answer."
        self.assertEqual(draft_preview(draft), draft)

    def test_a_long_draft_keeps_its_beginning_and_its_end(self):
        draft = "START" + ("x" * 5000) + "END"
        preview = draft_preview(draft)
        self.assertLess(len(preview), len(draft))
        self.assertTrue(preview.startswith("START"))
        self.assertTrue(preview.endswith("END"))
        self.assertIn("omitted", preview)


class AnswerCheckerTests(unittest.TestCase):
    LONG_DRAFT = ("The acceleration is 9.81 m/s^2. " * 20).strip()

    def test_an_empty_draft_fails(self):
        result = check_answer(query="q", draft="", state={})
        self.assertEqual(result.verdict, "failed")

    def test_a_short_acknowledgement_is_not_worth_reviewing(self):
        result = check_answer(query="q", draft="Nice work!", state={})
        self.assertEqual(result.verdict, VERDICT_SKIPPED)

    def test_a_clean_draft_passes_through_unchanged(self):
        with patch(
            "agents.answer_checker.groq_complete",
            return_value=('{"verdict": "pass", "issues": []}', "m"),
        ):
            result = check_answer(query="q", draft=self.LONG_DRAFT, state={})
        self.assertEqual(result.verdict, VERDICT_PASS)
        self.assertEqual(result.answer, self.LONG_DRAFT)

    def test_a_bad_draft_is_replaced_by_the_correction(self):
        import json

        fixed = ("The acceleration is actually 9.8 m/s^2, and here is why. " * 20).strip()
        payload = json.dumps(
            {
                "verdict": "revise",
                "issues": ["wrong value"],
                "revised_answer": fixed,
            }
        )
        with patch("agents.answer_checker.groq_complete", return_value=(payload, "m")):
            result = check_answer(query="q", draft=self.LONG_DRAFT, state={})
        self.assertEqual(result.verdict, VERDICT_REVISED)
        self.assertEqual(result.answer, fixed)
        self.assertEqual(result.issues, ["wrong value"])

    def test_a_checker_outage_never_blocks_the_student(self):
        with patch(
            "agents.answer_checker.groq_complete", side_effect=RuntimeError("groq down")
        ):
            result = check_answer(query="q", draft=self.LONG_DRAFT, state={})
        self.assertEqual(result.verdict, VERDICT_SKIPPED)
        self.assertEqual(result.answer, self.LONG_DRAFT)

    def test_unparseable_checker_output_never_blocks_the_student(self):
        with patch(
            "agents.answer_checker.groq_complete",
            return_value=("I think it's fine honestly", "m"),
        ):
            result = check_answer(query="q", draft=self.LONG_DRAFT, state={})
        self.assertEqual(result.verdict, VERDICT_SKIPPED)
        self.assertEqual(result.answer, self.LONG_DRAFT)

    def test_json_wrapped_in_a_code_fence_is_still_read(self):
        with patch(
            "agents.answer_checker.groq_complete",
            return_value=('```json\n{"verdict": "pass", "issues": []}\n```', "m"),
        ):
            result = check_answer(query="q", draft=self.LONG_DRAFT, state={})
        self.assertEqual(result.verdict, VERDICT_PASS)

    def test_a_truncated_revision_is_rejected_in_favour_of_the_draft(self):
        """A 'correction' that drops most of the answer is a broken checker."""
        with patch(
            "agents.answer_checker.groq_complete",
            return_value=(
                '{"verdict": "revise", "issues": ["x"], "revised_answer": "Too short."}',
                "m",
            ),
        ):
            result = check_answer(query="q", draft=self.LONG_DRAFT, state={})
        self.assertEqual(result.verdict, VERDICT_SKIPPED)
        self.assertEqual(result.answer, self.LONG_DRAFT)

    def test_the_verdict_lands_in_the_trace(self):
        from agents.runtime.trace import AgentTrace

        trace = AgentTrace()
        with patch(
            "agents.answer_checker.groq_complete",
            return_value=('{"verdict": "pass", "issues": []}', "m"),
        ):
            check_answer(query="q", draft=self.LONG_DRAFT, state={}, trace=trace)
        self.assertEqual(trace.checker_verdict, VERDICT_PASS)
        self.assertEqual(trace.path(), ["check:answer_checker"])


if __name__ == "__main__":
    unittest.main()
