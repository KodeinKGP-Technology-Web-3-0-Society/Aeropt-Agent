"""Unit tests for shared turn and session service."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from services.turn_service import (
    build_initial_state,
    clear_stale_practice_carry,
    normalize_content_type,
    parse_legacy_answer,
    parse_turn_input,
    resolve_session,
    update_carry,
)


class TurnServiceTests(unittest.TestCase):
    def test_normalize_content_type(self):
        self.assertEqual(normalize_content_type("past year question"), "pyq")
        self.assertEqual(normalize_content_type("SOLVED EXAMPLE"), "example")
        self.assertEqual(normalize_content_type("Practice Question"), "practice")
        self.assertEqual(normalize_content_type("theory"), "theory")

    def test_parse_turn_input_ui_button_outcome(self):
        carry = {"awaiting_graded_response": True, "content_type_requested": "practice"}
        query, overrides, was_graded = parse_turn_input("", carry, graded_outcome="correct")
        self.assertTrue(was_graded)
        self.assertIsNotNone(overrides)
        self.assertTrue(overrides["is_correct"])
        self.assertEqual(overrides["student_answer"], "1")

        query, overrides, was_graded = parse_turn_input("", carry, graded_outcome="incorrect")
        self.assertTrue(was_graded)
        self.assertFalse(overrides["is_correct"])
        self.assertEqual(overrides["student_answer"], "0")

    def test_parse_turn_input_raw_digits_when_awaiting(self):
        carry = {"awaiting_graded_response": True, "content_type_requested": "practice"}
        query, overrides, was_graded = parse_turn_input("1", carry)
        self.assertTrue(was_graded)
        self.assertTrue(overrides["is_correct"])

        query, overrides, was_graded = parse_turn_input("0", carry)
        self.assertTrue(was_graded)
        self.assertFalse(overrides["is_correct"])

    def test_parse_turn_input_raw_digits_not_graded_when_not_awaiting(self):
        carry = {"awaiting_graded_response": False}
        query, overrides, was_graded = parse_turn_input("1", carry)
        self.assertFalse(was_graded)
        self.assertIsNone(overrides)
        self.assertEqual(query, "1")

    def test_parse_turn_input_legacy_answer(self):
        carry = {}
        raw = "ANSWER::Option B::correct::None::45"
        query, overrides, was_graded = parse_turn_input(raw, carry)
        self.assertTrue(was_graded)
        self.assertTrue(overrides["is_correct"])
        self.assertEqual(overrides["student_answer"], "Option B")
        self.assertEqual(overrides["time_taken_sec"], 45)

    def test_clear_stale_practice_carry(self):
        carry = {
            "awaiting_graded_response": True,
            "retrieved_chunks": ["chunk1"],
            "content_type_requested": "practice",
            "primary_concept_id": "c1",
        }
        clear_stale_practice_carry(carry)
        self.assertNotIn("awaiting_graded_response", carry)
        self.assertNotIn("retrieved_chunks", carry)
        self.assertNotIn("content_type_requested", carry)
        self.assertEqual(carry.get("primary_concept_id"), "c1")

    def test_update_carry_graded_clears_awaiting(self):
        carry = {"awaiting_graded_response": True, "retrieved_chunks": ["c1"]}
        result = {"awaiting_graded_response": None}
        update_carry(carry, result, was_graded=True)
        self.assertNotIn("awaiting_graded_response", carry)
        self.assertNotIn("retrieved_chunks", carry)

    def test_build_initial_state(self):
        carry = {"primary_concept_id": "concept_abc", "presentation_by_turn": {"0": {}}}
        state = build_initial_state(
            user_id="user-123",
            session_id="sess-456",
            query="explain friction",
            carry=carry,
        )
        self.assertEqual(state["user_id"], "user-123")
        self.assertEqual(state["session_id"], "sess-456")
        self.assertEqual(state["query"], "explain friction")
        self.assertEqual(state.get("primary_concept_id"), "concept_abc")
        self.assertNotIn("presentation_by_turn", state)

    @patch("services.turn_service.db")
    def test_resolve_session_new_and_existing(self, mock_db):
        mock_db.create_session.return_value = {"session_id": "new-session-id"}
        new_id = resolve_session("user-1", None)
        self.assertEqual(new_id, "new-session-id")

        mock_db.read_session.return_value = {"session_id": "exist-id", "user_id": "user-1"}
        exist_id = resolve_session("user-1", "exist-id")
        self.assertEqual(exist_id, "exist-id")

        mock_db.read_session.return_value = None
        with self.assertRaises(KeyError):
            resolve_session("user-1", "bad-id")


if __name__ == "__main__":
    unittest.main()
