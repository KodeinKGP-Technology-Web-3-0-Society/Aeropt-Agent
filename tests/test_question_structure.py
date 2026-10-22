"""Questions reach the screen as structure and are graded from it (local_ingest/structure.py,
services/answer_key.py). The failures these pin down were seen in the practice pane."""

from __future__ import annotations

from local_ingest import structure
from services import answer_key


def test_a_figure_that_mentions_option_labels_does_not_become_the_options():
    text = ("For photo-electric effect, identify the correct variation(s) of $V_0$ with $\\lambda$.\n\n"
            "[Figure: Four option graphs labeled (A), (B), (C), and (D), each with $V_0$ on the vertical axis.]")
    stem, figure = structure.split_figure(text)
    assert "(A)" not in stem and "labeled (A), (B), (C), and (D)" in figure
    assert structure.split_inline(stem) is None


def test_lettered_parts_are_parts_not_options():
    text = "A ball is thrown up at 20 m/s. (a) How high does it go? (b) How long is it in the air? (c) Its speed at 10 m? (d) Its speed on return?"
    assert structure.split_inline(text) is None
    lead, parts = structure.split_parts(text)
    assert lead.startswith("A ball") and [p["label"] for p in parts] == ["a", "b", "c", "d"]


def test_a_structure_that_changes_a_number_is_refused():
    original = "A block of mass 2 kg slides 5 m. Find the work done. (A) 10 J (B) 20 J (C) 30 J (D) 40 J"
    good = {"stem": "A block of mass 2 kg slides 5 m. Find the work done.", "figure": "",
            "options": [{"label": l, "text": f"{n} J"} for l, n in zip("ABCD", (10, 20, 30, 40))]}
    assert structure.faithful(original, good) is None
    bad = dict(good, stem="A block of mass 3 kg slides 5 m. Find the work done.")
    assert "numbers changed" in structure.faithful(original, bad)


def test_grading_uses_the_structured_official_answer():
    multi = {"structured": {"answer": {"kind": "letters", "letters": "AC"}}}
    assert answer_key.grade_structured(multi, ["C", "A"])["correct"]
    assert not answer_key.grade_structured(multi, ["A"])["correct"]  # partial is not full marks
    numeric = {"structured": {"answer": {"kind": "range", "low": 1.15, "high": 1.25}}}
    assert answer_key.grade_structured(numeric, value="1.2")["correct"] is True
    assert answer_key.grade_structured(numeric, value="1.3")["correct"] is False
    trailing = answer_key.grade_structured(numeric, value="1.2 m/s")
    assert trailing["grade_status"] == "invalid_submission"
    assert trailing["correct"] is None
    with_unit = {"structured": {"answer": {"kind": "range", "low": 1.15, "high": 1.25, "unit": "m/s"}}}
    assert answer_key.grade_structured(with_unit, value="1.2 m/s")["correct"] is True
    prose = answer_key.grade_structured(numeric, value="about ten")
    assert prose["grade_status"] == "invalid_submission"
    assert prose["correct"] is None


def test_the_old_text_key_reads_every_letter_and_prefers_the_official_line():
    assert answer_key.parse_key("Official answer: A, C, D (key)\n\nWorked solution … Answer: B") == frozenset({1, 3, 4})
