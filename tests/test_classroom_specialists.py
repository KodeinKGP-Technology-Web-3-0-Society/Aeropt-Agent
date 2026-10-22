"""Classroom scheduling rules with model proposals stubbed at the planner boundary."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from services.classroom_specialists import (
    daily_specialist,
    load_learner,
    plan_specialist,
    readiness_specialist,
    revise_specialist,
)


def _body(**overrides):
    base = {
        "for_date": "2026-09-26",
        "home": {
            "today": None,
            "plan": [],
            "map": [],
            "revise": [],
            "readiness": {"score": 0, "label": "early"},
        },
        "catalog": [],
        "trackers": [],
    }
    base.update(overrides)
    if "home" in overrides and isinstance(overrides["home"], dict):
        home = {
            "today": None,
            "plan": [],
            "map": [],
            "revise": [],
            "readiness": {"score": 0, "label": "early"},
        }
        home.update(overrides["home"])
        base["home"] = home
    return base


class _OfflinePlannerTests(unittest.TestCase):
    def setUp(self):
        # Empty proposals exercise the deterministic fallback without calling a
        # provider. Individual tests can supply a model proposal explicitly.
        planner_patch = patch("services.study_plan_generator.generate_study_plan", return_value=[])
        self.planner = planner_patch.start()
        self.addCleanup(planner_patch.stop)


class DailySpecialistTests(_OfflinePlannerTests):
    def test_prefers_due_revision_over_plan_concept(self):
        learner = load_learner(
            _body(
                home={
                    "plan": [
                        {
                            "concept_id": "torque",
                            "title": "Torque",
                            "status": "in_progress",
                            "when": "today",
                        }
                    ],
                    "revise": [
                        {
                            "concept_id": "newton2",
                            "title": "Newton's second law",
                            "due": "today",
                        }
                    ],
                    "readiness": {"score": 50, "label": "building"},
                }
            )
        )
        today = daily_specialist(learner)["today"]
        self.assertIsNotNone(today)
        self.assertEqual(today["concept_id"], "newton2")
        self.assertEqual(today["action"], "revise")
        self.assertEqual(today["minutes"], 25)
        self.assertEqual(today["for_date"], "2026-09-26")

    def test_falls_through_to_plan_when_revise_empty(self):
        learner = load_learner(
            _body(
                home={
                    "plan": [
                        {
                            "concept_id": "torque",
                            "title": "Torque",
                            "status": "in_progress",
                            "when": "today",
                        }
                    ],
                    "revise": [],
                    "readiness": {"score": 20, "label": "early"},
                }
            )
        )
        today = daily_specialist(learner)["today"]
        self.assertEqual(today["concept_id"], "torque")
        self.assertEqual(today["action"], "lesson")

    def test_assess_only_when_building_and_mastery_ge_50(self):
        learner = load_learner(
            _body(
                home={
                    "plan": [],
                    "revise": [],
                    "map": [
                        {
                            "concept_id": "energy",
                            "title": "Energy",
                            "chapter": "Work",
                            "mastery": 55,
                            "prereqs": [],
                        }
                    ],
                    "readiness": {"score": 55, "label": "building"},
                }
            )
        )
        today = daily_specialist(learner)["today"]
        self.assertEqual(today["concept_id"], "energy")
        self.assertEqual(today["action"], "assess")

    def test_null_when_nothing_qualifies(self):
        learner = load_learner(
            _body(
                home={
                    "plan": [],
                    "revise": [],
                    "map": [
                        {
                            "concept_id": "energy",
                            "title": "Energy",
                            "chapter": "Work",
                            "mastery": 55,
                            "prereqs": [],
                        }
                    ],
                    "readiness": {"score": 20, "label": "early"},
                }
            )
        )
        self.assertIsNone(daily_specialist(learner)["today"])

    def test_weak_tracker_beats_an_empty_home_revise_list(self):
        learner = load_learner(
            _body(
                home={
                    "plan": [
                        {
                            "concept_id": "torque",
                            "title": "Torque",
                            "status": "in_progress",
                            "when": "today",
                        }
                    ],
                    "revise": [],
                },
                trackers=[
                    {
                        "concept_id": "friction",
                        "title": "Friction",
                        "chapter": "Laws of motion",
                        "mastery": 30,
                        "attempts": 2,
                        "flagged_weak": True,
                    }
                ],
            )
        )
        today = daily_specialist(learner)["today"]
        self.assertEqual(today["concept_id"], "friction")
        self.assertEqual(today["action"], "revise")

    def test_skip_moves_past_a_dismissed_revision(self):
        learner = load_learner(
            _body(
                skip_concept_ids=["newton2"],
                home={
                    "plan": [
                        {
                            "concept_id": "torque",
                            "title": "Torque",
                            "status": "in_progress",
                            "when": "today",
                        }
                    ],
                    "revise": [
                        {
                            "concept_id": "newton2",
                            "title": "Newton's second law",
                            "due": "today",
                        }
                    ],
                },
            )
        )
        today = daily_specialist(learner)["today"]
        self.assertEqual(today["concept_id"], "torque")
        self.assertEqual(today["action"], "lesson")


class PlanSpecialistTests(_OfflinePlannerTests):
    def test_does_not_emit_concept_absent_from_catalog(self):
        learner = load_learner(
            _body(
                catalog=[
                    {"concept_id": "torque", "title": "Torque", "chapter": "Rotation"},
                ],
                home={
                    "plan": [
                        {
                            "concept_id": "ghost",
                            "title": "Ghost",
                            "status": "pending",
                            "when": "this_week",
                        }
                    ],
                    "map": [
                        {
                            "concept_id": "ghost",
                            "title": "Ghost",
                            "chapter": "X",
                            "mastery": 10,
                            "prereqs": [],
                        }
                    ],
                },
                trackers=[
                    {
                        "concept_id": "ghost",
                        "title": "Ghost",
                        "chapter": "X",
                        "mastery": 10,
                        "attempts": 1,
                        "flagged_weak": False,
                    }
                ],
            )
        )
        plan = plan_specialist(learner)["plan"]
        ids = [row["concept_id"] for row in plan]
        self.assertEqual(ids, ["torque"])
        self.assertNotIn("ghost", ids)

    def test_omits_mastery_ge_80(self):
        learner = load_learner(
            _body(
                catalog=[
                    {"concept_id": "done", "title": "Done", "chapter": "A"},
                    {"concept_id": "next", "title": "Next", "chapter": "A"},
                ],
                trackers=[
                    {
                        "concept_id": "done",
                        "title": "Done",
                        "chapter": "A",
                        "mastery": 80,
                        "attempts": 3,
                        "flagged_weak": False,
                    }
                ],
            )
        )
        plan = plan_specialist(learner)["plan"]
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["concept_id"], "next")
        self.assertEqual(plan[0]["status"], "in_progress")
        self.assertEqual(plan[0]["when"], "today")

    def test_filters_model_proposal_and_preserves_valid_order_and_actions(self):
        learner = load_learner(_body(
            catalog=[
                {"concept_id": "done", "title": "Done"},
                {"concept_id": "torque", "title": "Torque"},
                {"concept_id": "energy", "title": "Energy"},
            ],
            trackers=[{"concept_id": "done", "mastery": 80, "attempts": 3}],
        ))
        self.planner.return_value = [
            {"concept_id": "ghost", "title": "Ghost", "action": "LEARN"},
            {"concept_id": "done", "title": "Done", "action": "PRACTICE"},
            {"concept_id": "energy", "title": "Energy", "action": "REVISE",
             "estimated_minutes": 15, "reason": "Review the work-energy theorem."},
            {"concept_id": "torque", "title": "Torque", "action": "LEARN",
             "status": "pending", "when": "this_week"},
        ]

        plan = plan_specialist(learner)["plan"]

        self.planner.assert_called_once_with({}, learner["trackers"], learner["catalog"])
        self.assertEqual([row["concept_id"] for row in plan], ["energy", "torque"])
        self.assertEqual(plan[0]["action"], "REVISE")
        self.assertEqual(plan[0]["estimated_minutes"], 15)
        self.assertEqual(plan[0]["status"], "in_progress")
        self.assertEqual(plan[0]["when"], "today")
        self.assertEqual(plan[1]["action"], "LEARN")
        self.assertEqual(plan[1]["status"], "pending")
        self.assertEqual(plan[1]["when"], "this_week")


class ReadinessSpecialistTests(_OfflinePlannerTests):
    def test_mean_of_two_attempted_trackers(self):
        learner = load_learner(
            _body(
                trackers=[
                    {
                        "concept_id": "a",
                        "title": "A",
                        "chapter": "X",
                        "mastery": 40,
                        "attempts": 2,
                        "flagged_weak": False,
                    },
                    {
                        "concept_id": "b",
                        "title": "B",
                        "chapter": "X",
                        "mastery": 60,
                        "attempts": 1,
                        "flagged_weak": False,
                    },
                    {
                        "concept_id": "c",
                        "title": "C",
                        "chapter": "X",
                        "mastery": 99,
                        "attempts": 0,
                        "flagged_weak": False,
                    },
                ]
            )
        )
        readiness = readiness_specialist(learner)["readiness"]
        self.assertEqual(readiness["score"], 50)
        self.assertEqual(readiness["label"], "building")


class ReviseSpecialistTests(_OfflinePlannerTests):
    def test_flags_weak_trackers_first_due_today(self):
        learner = load_learner(
            _body(
                home={
                    "revise": [
                        {
                            "concept_id": "old",
                            "title": "Old",
                            "due": "today",
                        }
                    ]
                },
                trackers=[
                    {
                        "concept_id": "weak1",
                        "title": "Weak One",
                        "chapter": "A",
                        "mastery": 20,
                        "attempts": 2,
                        "flagged_weak": True,
                    },
                    {
                        "concept_id": "ok",
                        "title": "OK",
                        "chapter": "A",
                        "mastery": 70,
                        "attempts": 2,
                        "flagged_weak": False,
                    },
                    {
                        "concept_id": "weak2",
                        "title": "Weak Two",
                        "chapter": "A",
                        "mastery": 15,
                        "attempts": 1,
                        "flagged_weak": True,
                    },
                ],
            )
        )
        revise = revise_specialist(learner)["revise"]
        self.assertEqual(
            [(r["concept_id"], r["due"]) for r in revise],
            [("weak1", "today"), ("weak2", "this_week")],
        )


if __name__ == "__main__":
    unittest.main()
