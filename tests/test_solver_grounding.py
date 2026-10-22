"""
Regression tests for the F2 fix: the Solver must be told plainly when it has no
(or weak) grounding, instead of the old "chunks are optional, compose freely"
instruction that let it hallucinate confidently on unresolved/low-confidence turns.
"""

from __future__ import annotations

import unittest

from agents.solver import _grounding_note, _format_chunks


class TestGroundingNote(unittest.TestCase):
    def test_unresolved_concept_is_flagged_plainly(self):
        note = _grounding_note({"retrieval_confidence": "unresolved"})
        self.assertIn("UNRESOLVED", note)
        self.assertIn("pre-indexed", note.lower())

    def test_low_confidence_is_flagged_as_hedge(self):
        note = _grounding_note({"retrieval_confidence": "low"})
        self.assertIn("LOW CONFIDENCE", note)

    def test_ok_confidence_is_normal(self):
        note = _grounding_note({"retrieval_confidence": "ok"})
        self.assertIn("OK", note)
        self.assertNotIn("UNRESOLVED", note)

    def test_missing_confidence_defaults_to_normal_not_a_crash(self):
        # e.g. meta/repair turns that reuse a prior turn's chunks without
        # retrieve_for_state running again this turn.
        note = _grounding_note({})
        self.assertIn("OK", note)

    def test_no_chunks_no_longer_invites_free_improvisation(self):
        text = _format_chunks([])
        self.assertNotIn("compose from physics knowledge", text)


if __name__ == "__main__":
    unittest.main()
