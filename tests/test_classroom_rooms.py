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
            raise AssertionError("db.read_concepts must not be called when locked")

        with patch("agents.concept_resolver.db.read_concepts", side_effect=_boom), patch(
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
            "agents.graphrag_retriever.graphrag_retriever_node",
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
        with patch(
            "services.classroom_rooms._solution_index",
            return_value={"q1": "12 N"},
        ), patch("services.classroom_rooms._record_diagnostic", return_value=40) as recorded, patch(
            "services.classroom_rooms.post_events"
        ) as posted:
            result = grade_submissions(
                "torque",
                [{"item_id": "q1", "answer": "5 N", "correct": True}],
                user_id="user-1",
                token="jwt",
            )
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["attempts"], 1)
        self.assertFalse(result["items"][0]["correct"])
        recorded.assert_called_once()
        self.assertFalse(recorded.call_args.kwargs["is_correct"])
        events = posted.call_args.args[0]
        self.assertEqual(events[0]["payload"]["correct"], False)
        self.assertEqual(events[-1]["kind"], "mastery")

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
