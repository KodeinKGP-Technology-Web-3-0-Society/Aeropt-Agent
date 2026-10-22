"""Classroom rooms stay on the tutor: locked concept, retrieved assess, keyed grade."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.concept_resolver import concept_resolver_node
from services.classroom_rooms import (
    build_assessment,
    grade_submissions,
    job_card,
    prepare_room,
)


class LockedConceptTests(unittest.TestCase):
    def test_lock_keeps_prerequisites_and_skips_the_catalog(self):
        state = {
            "query": "explain friction instead",
            "recent_turns": [],
            "locked_concept_id": "torque",
            "study_stage": "example",
        }

        def _boom():
            raise AssertionError("The concept catalogue must not be read when locked")

        with patch("agents.concept_resolver.store.all_concepts", side_effect=_boom), patch(
            "agents.concept_resolver.prerequisite_ids", return_value=["newton2"]
        ):
            result = concept_resolver_node(state)

        self.assertEqual(result["primary_concept_id"], "torque")
        self.assertEqual(result["secondary_concept_ids"], ["newton2"])
        self.assertEqual(result["content_type_requested"], "example")


class JobCardTests(unittest.TestCase):
    def test_lesson_names_prerequisites(self):
        text = job_card("lesson", "torque", ["newton2"])
        self.assertIn("torque", text)
        self.assertIn("newton2", text)
        self.assertIn("only voice", text)

    def test_prepare_assess_uses_a_shorter_budget(self):
        with patch("services.classroom_rooms.build_assessment", return_value={"items": []}), patch(
            "services.classroom_rooms.prerequisite_ids", return_value=["newton2"]
        ):
            room = prepare_room(surface="assess", concept_id="torque", assess_action="build")
        self.assertIsNotNone(room)
        overrides = room["state_overrides"]
        self.assertEqual(overrides["classroom_surface"], "assess")
        self.assertEqual(overrides["locked_concept_id"], "torque")
        self.assertEqual(overrides["secondary_concept_ids"], ["newton2"])
        self.assertEqual(overrides["classroom_max_steps"], 4)
        self.assertIn("Do not invent questions", overrides["classroom_job"])


class BuildAssessmentTests(unittest.TestCase):
    def test_items_come_from_retrieved_chunks(self):
        with patch(
            "retrieval.bridge.retrieve_for_state",
            return_value={
                "retrieved_chunks": [
                    {"content_id": "q1", "core_text": "A rod of length L."},
                    {"content_id": "q2", "core_text": ""},
                ]
            },
        ):
            built = build_assessment("torque")
        self.assertEqual(built["items"], [{"item_id": "q1", "prompt": "A rod of length L."}])


class GradeSubmissionTests(unittest.TestCase):
    def test_client_correct_flag_does_not_override_the_key(self):
        question = {
            "content_id": "q1",
            "solution": "A long worked solution that is not the answer key.",
            "structured": {"answer": {"kind": "integer", "value": 12}},
        }
        with patch(
            "services.classroom_rooms._solution_index",
            return_value={"q1": question},
        ), patch("services.grading.db") as grading_db, patch(
            "services.classroom_rooms.post_events"
        ) as posted:
            grading_db.commit_graded_attempt.return_value = {
                "status": "committed",
                "attempt_id": "a-1",
                "is_correct": False,
                "grade_status": "incorrect",
                "evidence_source": "answer_key",
                "proficiency_before": 0.5,
                "proficiency_after": 0.35,
            }
            result = grade_submissions(
                "torque",
                [{"item_id": "q1", "answer": "5", "correct": True}],
                user_id="00000000-0000-0000-0000-000000000001",
                session_id="00000000-0000-0000-0000-000000000002",
                token="jwt",
            )
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["attempts"], 1)
        self.assertFalse(result["items"][0]["correct"])
        self.assertEqual(result["items"][0]["grade_status"], "incorrect")
        request = grading_db.commit_graded_attempt.call_args.args[0]
        self.assertFalse(request["is_correct"])
        self.assertEqual(request["evidence_source"], "answer_key")
        events = posted.call_args.args[0]
        self.assertEqual(events[0]["payload"]["correct"], False)

    def test_long_solution_without_a_key_is_not_marked_incorrect(self):
        with patch(
            "services.classroom_rooms._solution_index",
            return_value={"q1": {"content_id": "q1", "solution": "Step 1. Step 2. Therefore the block stops."}},
        ), patch("services.classroom_rooms.post_events") as posted:
            result = grade_submissions(
                "torque",
                [{"item_id": "q1", "answer": "B", "correct": True}],
                user_id="user-1",
            )
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["ungraded"], 1)
        self.assertEqual(result["items"][0]["grade_status"], "ungradable")
        posted.assert_not_called()

    def test_missing_key_is_ungraded(self):
        with patch("services.classroom_rooms._solution_index", return_value={}), patch(
            "services.classroom_rooms.post_events"
        ) as posted:
            result = grade_submissions(
                "torque",
                [{"item_id": "q1", "answer": "12 N", "correct": True}],
            )
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["ungraded"], 1)
        posted.assert_not_called()


if __name__ == "__main__":
    unittest.main()
