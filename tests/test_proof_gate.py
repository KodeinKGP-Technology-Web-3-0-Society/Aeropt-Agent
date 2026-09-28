"""A numeric draft is tool-checked before the student sees it."""

from __future__ import annotations

import unittest

from agents.runtime.proof import already_checked, ensure_proof, needs_proof
from agents.runtime.trace import AgentTrace, TraceStep


def _trace(*names: str) -> AgentTrace:
    trace = AgentTrace(user_id="u", session_id="s")
    for name in names:
        trace.add(TraceStep(kind="tool", name=name))
    return trace


class ProofGateTests(unittest.TestCase):
    def test_plain_explanation_is_left_alone(self):
        self.assertFalse(needs_proof("Torque is the turning effect of a force."))
        text = "Torque is the turning effect of a force."
        self.assertEqual(ensure_proof(text, _trace()), text)

    def test_numeric_draft_without_a_check_cannot_pass_unverified(self):
        trace = _trace("retrieve_content")
        out = ensure_proof("The force is 12 N.", trace)
        self.assertIn("Checked with", out)
        self.assertTrue(already_checked(trace))
        names = [step.name for step in trace.steps]
        self.assertTrue({"calculate", "check_dimensions"} & set(names))

    def test_existing_physics_tool_is_enough(self):
        trace = _trace("calculate")
        out = ensure_proof("The force is 12 N.", trace)
        self.assertEqual(out, "The force is 12 N.")
        self.assertEqual([step.name for step in trace.steps], ["calculate"])


if __name__ == "__main__":
    unittest.main()
