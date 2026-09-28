"""Classroom evidence is one HTTP post after a grade, and it never blocks the reply."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from services.classroom_evidence import events_for_turn, post_turn_evidence, resolve_learner_token


class EventsForTurnTests(unittest.TestCase):
    def test_button_posts_one_attempt_as_evaluation(self):
        events = events_for_turn(
            result={
                "primary_concept_id": "torque",
                "is_correct": True,
                "attempt_recorded": True,
                "mastery_pct": 72,
            },
            was_graded=True,
            study_stage="practice",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "attempt")
        self.assertEqual(events[0]["specialist"], "evaluation")
        self.assertEqual(events[0]["surface"], "assess")
        self.assertEqual(events[0]["payload"], {"correct": True, "mastery": 72})

    def test_lesson_record_posts_attempt_and_mastery(self):
        events = events_for_turn(
            result={
                "recorded_concept_id": "torque",
                "is_correct": False,
                "attempt_recorded": True,
                "mastery_pct": 41,
                "study_stage": "theory",
            },
            was_graded=False,
        )
        self.assertEqual([e["kind"] for e in events], ["attempt", "mastery"])
        self.assertEqual(events[0]["specialist"], "tutor")
        self.assertEqual(events[0]["surface"], "lesson")
        self.assertEqual(events[1]["payload"], {"mastery": 41})

    def test_free_text_without_a_recorded_attempt_posts_nothing(self):
        self.assertEqual(
            events_for_turn(
                result={"primary_concept_id": "torque", "is_correct": False},
                was_graded=False,
            ),
            [],
        )

    def test_missing_concept_posts_nothing(self):
        self.assertEqual(
            events_for_turn(
                result={"is_correct": True, "attempt_recorded": True},
                was_graded=True,
            ),
            [],
        )

    def test_failed_grade_posts_nothing(self):
        self.assertEqual(
            events_for_turn(
                result={
                    "primary_concept_id": "torque",
                    "is_correct": False,
                    "attempt_record_failed": True,
                },
                was_graded=True,
            ),
            [],
        )


class PostTurnEvidenceTests(unittest.TestCase):
    def test_django_down_does_not_raise(self):
        with patch(
            "services.classroom_evidence.urllib.request.urlopen",
            side_effect=urllib.error.URLError("down"),
        ):
            post_turn_evidence(
                result={"primary_concept_id": "torque", "is_correct": False},
                was_graded=True,
                token="jwt",
                base_url="http://127.0.0.1:8000",
            )

    def test_posts_the_attempt_body(self):
        captured = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _open(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return _Resp(b"{}")

        with patch("services.classroom_evidence.urllib.request.urlopen", side_effect=_open):
            post_turn_evidence(
                result={"roadmap_concept_id": "torque", "is_correct": True},
                was_graded=True,
                study_stage="theory",
                token="Bearer jwt-1",
                base_url="http://127.0.0.1:8000/",
            )

        self.assertEqual(captured["url"], "http://127.0.0.1:8000/api/v1/classroom/evidence/")
        self.assertEqual(captured["auth"], "Bearer jwt-1")
        self.assertEqual(captured["body"]["kind"], "attempt")
        self.assertEqual(captured["body"]["concept_id"], "torque")
        self.assertLessEqual(captured["timeout"], 2)

    def test_skips_without_a_token(self):
        with patch("services.classroom_evidence.urllib.request.urlopen") as opened:
            post_turn_evidence(
                result={"primary_concept_id": "torque", "is_correct": True},
                was_graded=True,
                token="",
                base_url="http://127.0.0.1:8000",
            )
        opened.assert_not_called()


class TokenTests(unittest.TestCase):
    def test_header_used_when_field_empty(self):
        self.assertEqual(resolve_learner_token(None, "Bearer abc"), "abc")

    def test_field_wins(self):
        self.assertEqual(resolve_learner_token("field", "Bearer abc"), "field")
