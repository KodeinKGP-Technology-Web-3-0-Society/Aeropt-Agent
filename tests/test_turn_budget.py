"""Tests for turn budget and deadline enforcement (F3).

Tests:
1. TurnBudget methods and child budget calculation.
2. A fake model that sleeps past the deadline still produces a forced answer within deadline + reserve.
3. An expert cannot push the turn past its deadline.
4. Answer checker is skipped with 'skipped:budget' when turn budget is exhausted.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from agents.runtime.budget import (
    BudgetExhausted,
    TurnBudget,
    DEFAULT_CLOSE_RESERVE_S,
    current_budget_var,
)
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import Toolbelt, ToolContext
from agents.runtime.trace import AgentTrace


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class TurnBudgetTests(unittest.TestCase):
    def test_budget_exhaustion_and_timeout_for(self):
        budget = TurnBudget(deadline=time.time() + 0.1)
        self.assertFalse(budget.is_exhausted())
        self.assertGreater(budget.remaining(), 0)

        # Timeout for 25s with 0.1s remaining is capped to ~0.1s
        to = budget.timeout_for(per_call_cap=25.0)
        self.assertLessEqual(to, 0.1)

        # Sleep past deadline
        time.sleep(0.15)
        self.assertTrue(budget.is_exhausted())
        self.assertEqual(budget.remaining(), 0.0)

        with self.assertRaises(BudgetExhausted):
            budget.timeout_for(per_call_cap=25.0)

    def test_child_budget_cannot_outlive_parent(self):
        # Parent with 10s remaining, 8s reserve -> max child time is 2s
        now = time.time()
        parent = TurnBudget(deadline=now + 10.0, close_reserve_s=8.0)
        child = parent.child_budget(own_budget_s=20.0, reserve_s=8.0)
        self.assertLessEqual(child.remaining(), 2.1)
        self.assertGreater(child.remaining(), 1.5)

    def test_child_budget_exhausted_when_reserve_exceeds_remaining(self):
        # Parent with 5s remaining, 8s reserve -> child gets 0s
        now = time.time()
        parent = TurnBudget(deadline=now + 5.0, close_reserve_s=8.0)
        child = parent.child_budget(own_budget_s=20.0, reserve_s=8.0)
        self.assertEqual(child.remaining(), 0.0)
        self.assertTrue(child.is_exhausted())

    def test_fake_model_sleeping_past_deadline_still_produces_forced_answer(self):
        """F3 Acceptance Test:
        A fake model that sleeps past the deadline still produces a forced answer
        within deadline + reserve.
        """
        trace = AgentTrace(user_id="u", session_id="s")
        # 0.2s budget with 0.5s close reserve
        budget = TurnBudget(deadline=time.time() + 0.2, close_reserve_s=0.5)
        ctx = ToolContext(user_id="u", session_id="s", trace=trace, budget=budget)
        toolbelt = Toolbelt([], ctx)

        call_count = 0

        def fake_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Step 1 sleeps past deadline and raises BudgetExhausted
                time.sleep(0.25)
                raise BudgetExhausted("Turn budget deadline exceeded")
            # Forced close call succeeds with final answer
            return FakeMessage(content="Here is the forced closing answer."), "fake-model"

        start_time = time.time()
        with patch("agents.runtime.loop.groq_chat", side_effect=fake_chat):
            res = run_agent_loop(
                system_prompt="You are a tutor.",
                user_message="Explain momentum.",
                toolbelt=toolbelt,
                model="test-model",
                max_steps=5,
                time_budget_s=0.2,
                client=MagicMock(),
                trace=trace,
            )
        elapsed = time.time() - start_time

        self.assertEqual(res.stop_reason, "time_budget")
        self.assertIn("forced closing answer", res.text)
        # Completed within deadline (0.2s) + reserve (0.5s) + small test margin
        self.assertLess(elapsed, 0.2 + 0.5 + 0.5)

    def test_expert_cannot_push_turn_past_deadline(self):
        """F3 Acceptance Test:
        An expert cannot push the turn past its deadline.
        """
        from mcp_tools.handoff import ConsultExpertArgs, consult_expert

        trace = AgentTrace(user_id="u", session_id="s")
        # Parent with 5s remaining, reserve 8s -> remaining - reserve <= 0
        budget = TurnBudget(deadline=time.time() + 5.0, close_reserve_s=8.0)
        ctx = ToolContext(user_id="u", session_id="s", trace=trace, budget=budget)

        res = consult_expert(
            ConsultExpertArgs(expert="verifier", task="Check this step thoroughly."),
            ctx,
        )
        self.assertIn("error", res)
        self.assertIn("budget exhausted", res["error"].lower())

    def test_checker_skipped_when_budget_exhausted(self):
        """Answer Checker is skipped with skipped:budget when turn budget is exhausted."""
        from agents.answer_checker import check_answer, CheckResult

        trace = AgentTrace(user_id="u", session_id="s")
        draft = (
            "Force is defined as the rate of change of momentum of a body. According to "
            "Newton's second law of motion, the net force acting upon an object is equal to the "
            "rate at which its momentum changes with time, F = d(mv)/dt."
        )
        # Mock groq_complete to raise BudgetExhausted
        with patch("agents.answer_checker.groq_complete", side_effect=BudgetExhausted("Turn budget deadline exceeded")):
            checked = check_answer(
                query="What is force?",
                draft=draft,
                state={},
                trace=trace,
                timeout=0.01,
            )
            self.assertEqual(trace.checker_verdict, "skipped:budget")
            self.assertEqual(checked.verdict, "skipped")


if __name__ == "__main__":
    unittest.main()
