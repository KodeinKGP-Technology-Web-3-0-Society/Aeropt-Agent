"""Tests for the honest proof gate: verifying final formulas without fabricating check claims."""

from __future__ import annotations

import unittest
from typing import Dict, Any

from agents.runtime.proof import already_checked, ensure_proof, needs_proof
from agents.runtime.trace import AgentTrace, TraceStep


def _trace(*names: str) -> AgentTrace:
    trace = AgentTrace(user_id="u", session_id="s")
    for name in names:
        trace.add(TraceStep(kind="tool", name=name))
    return trace


class ProofGateTests(unittest.TestCase):
    def test_plain_explanation_is_left_alone(self):
        text = "Torque is the turning effect of a force."
        self.assertFalse(needs_proof(text))
        trace = _trace()
        self.assertEqual(ensure_proof(text, trace), text)
        notes = [s.name for s in trace.steps if s.kind == "note"]
        self.assertNotIn("proof=passed", notes)

    def test_existing_physics_tool_is_enough(self):
        trace = _trace("calculate")
        text = "The force is 12 N."
        out = ensure_proof(text, trace)
        self.assertEqual(out, text)
        self.assertEqual([step.name for step in trace.steps], ["calculate"])

    def test_irrelevant_number_does_not_claim_check(self):
        # A draft whose first number is irrelevant must not produce "Checked with"
        trace = _trace("retrieve_content")
        draft = "Step 1: Consider a 2 kg mass on an incline. What is the normal force?"
        out = ensure_proof(draft, trace)
        self.assertNotIn("Checked with", out)
        self.assertEqual(out, draft)
        notes = [s.name for s in trace.steps if s.kind == "note"]
        self.assertIn("proof=unverifiable", notes)

    def test_valid_final_formula_passes(self):
        trace = _trace("retrieve_content")
        draft = r"Therefore the speed is $v = \sqrt{2*g*h}$."
        out = ensure_proof(draft, trace)
        self.assertNotIn("Checked with", out)
        self.assertEqual(out, draft)
        notes = [s.name for s in trace.steps if s.kind == "note"]
        self.assertIn("proof=passed", notes)
        tool_steps = [s for s in trace.steps if s.kind == "tool"]
        self.assertTrue(any(s.name == "check_dimensions" and s.ok for s in tool_steps))

    def test_dimensionally_wrong_formula_reaches_checker_as_issue(self):
        trace = _trace("retrieve_content")
        state: Dict[str, Any] = {}
        draft = r"The acceleration is given by $a = g*h$."
        out = ensure_proof(draft, trace, state=state)
        # Check failed: do not append anything to the draft
        self.assertNotIn("Checked with", out)
        self.assertEqual(out, draft)
        notes = [s.name for s in trace.steps if s.kind == "note"]
        self.assertIn("proof=failed", notes)
        # Passed to checker state as a known issue
        self.assertIn("proof_issue", state)
        self.assertIn("NOT consistent", state["proof_issue"])
        self.assertIn("a = g*h", state["proof_issue"])

    def test_boxed_dimensionally_wrong_formula_detected(self):
        trace = _trace("retrieve_content")
        state: Dict[str, Any] = {}
        draft = r"The final result is $\boxed{v = \sqrt{2*g*h^2}}$."
        out = ensure_proof(draft, trace, state=state)
        self.assertEqual(out, draft)
        self.assertIn("proof_issue", state)
        notes = [s.name for s in trace.steps if s.kind == "note"]
        self.assertIn("proof=failed", notes)


if __name__ == "__main__":
    unittest.main()
