"""Parallel subject plan: the queue, milestones, and the together metric."""

from datetime import date

from services.study_plan import interleave_subjects, parallel_milestones, subject_board


def test_interleave_alternates_subjects_and_keeps_order_inside_one():
    queue = [
        {"subject": "Physics", "unit": "P1", "kind": "learn", "topic": "a"},
        {"subject": "Physics", "unit": "P1", "kind": "learn", "topic": "b"},
        {"subject": "Chemistry", "unit": "C1", "kind": "learn", "topic": "c"},
        {"subject": "Mathematics", "unit": "M1", "kind": "learn", "topic": "d"},
    ]
    out = interleave_subjects(queue)
    assert [item["subject"] for item in out] == ["Physics", "Chemistry", "Mathematics", "Physics"]
    assert [item["topic"] for item in out if item["subject"] == "Physics"] == ["a", "b"]


def test_started_topics_stay_ahead_and_still_alternate():
    queue = [
        {"subject": "Physics", "kind": "continue", "unit": "P", "topic": "p"},
        {"subject": "Physics", "kind": "learn", "unit": "P", "topic": "n"},
        {"subject": "Chemistry", "kind": "continue", "unit": "C", "topic": "c"},
        {"subject": "Mathematics", "kind": "learn", "unit": "M", "topic": "m"},
    ]
    out = interleave_subjects(queue)
    assert [item["kind"] for item in out] == ["continue", "continue", "learn", "learn"]
    assert [item["subject"] for item in out[:2]] == ["Physics", "Chemistry"]
    assert [item["subject"] for item in out[2:]] == ["Physics", "Mathematics"]


def test_milestones_pair_the_next_unfinished_unit_of_each_subject():
    units = [
        {"subject": "Physics", "unit": "Done", "total": 2, "mastered": 2, "started": 2},
        {"subject": "Physics", "unit": "Kinematics", "total": 3, "mastered": 0, "started": 1},
        {"subject": "Chemistry", "unit": "Mole", "total": 2, "mastered": 0, "started": 0},
        {"subject": "Mathematics", "unit": "Sets", "total": 2, "mastered": 0, "started": 0},
        {"subject": "Physics", "unit": "Laws", "total": 2, "mastered": 0, "started": 0},
    ]
    finish = {
        ("Physics", "Kinematics"): date(2026, 4, 2),
        ("Chemistry", "Mole"): date(2026, 4, 4),
        ("Mathematics", "Sets"): date(2026, 4, 3),
        ("Physics", "Laws"): date(2026, 4, 20),
    }
    milestones = parallel_milestones(units, finish)
    assert milestones[0]["units"] == ["Kinematics", "Mole", "Sets"]
    assert milestones[0]["by"] == date(2026, 4, 4)
    assert [part["subject"] for part in milestones[0]["subjects"]] == ["Physics", "Chemistry", "Mathematics"]
    assert milestones[1]["subjects"] == [{"subject": "Physics", "unit": "Laws", "by": date(2026, 4, 20)}]
    assert "Done" not in milestones[0]["units"]


def test_together_percent_is_the_furthest_behind_subject():
    units = [
        {"subject": "Physics", "unit": "P", "total": 10, "mastered": 1, "started": 2},
        {"subject": "Chemistry", "unit": "C", "total": 10, "mastered": 4, "started": 5},
        {"subject": "Mathematics", "unit": "M", "total": 10, "mastered": 0, "started": 1},
    ]
    finish = {
        ("Physics", "P"): date(2026, 5, 1),
        ("Chemistry", "C"): date(2026, 5, 1),
        ("Mathematics", "M"): date(2026, 6, 1),
    }
    board = subject_board(units, finish)
    assert board["percent"] == 10
    assert board["behind"] == "Mathematics"
    assert board["level"] is False
    assert board["subjects"][2]["next_unit"] == "M"
    assert board["subjects"][2]["finish_by"] == date(2026, 6, 1)


def test_together_is_level_when_every_subject_matches():
    units = [
        {"subject": "Physics", "unit": "P", "total": 4, "mastered": 1, "started": 1},
        {"subject": "Chemistry", "unit": "C", "total": 8, "mastered": 2, "started": 2},
    ]
    board = subject_board(units, {})
    assert board["percent"] == 25
    assert board["level"] is True
