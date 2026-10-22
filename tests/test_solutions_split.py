"""The answer stored beside a solution: where it comes from and that grading uses it."""

import json

from local_ingest.push_index import answer_for
from services.answer_key import grade_structured, stored_answer

OPTIONS = [{"label": "A", "text": "1"}, {"label": "B", "text": "2"}, {"label": "C", "text": "3"}, {"label": "D", "text": "4"}]


def test_the_sources_own_structured_answer_wins():
    it = {"structured": {"options": OPTIONS, "answer": {"kind": "letters", "letters": "B"}}, "solution": "Answer: C"}
    assert answer_for(it) == {"kind": "letters", "letters": "B"}


def test_key_line_is_read_for_a_question_with_options():
    it = {"structured": {"options": OPTIONS, "answer": None}, "solution": "Working...\nAnswer: C"}
    assert answer_for(it) == {"kind": "letters", "letters": "C"}


def test_multiple_correct_key_line_gives_every_letter():
    it = {"structured": {"options": OPTIONS}, "solution": "Correct option: A, C"}
    assert answer_for(it) == {"kind": "letters", "letters": "AC"}


def test_key_line_is_not_read_for_a_numeric_question():
    # "Answer: 3" on a question with no options is a number, not option C.
    it = {"structured": {"options": None}, "solution": "Answer: 3"}
    assert answer_for(it) is None


def test_no_structure_and_no_key_gives_no_answer():
    assert answer_for({"structured": None, "solution": "just working"}) is None


def test_grading_uses_the_stored_answer_when_the_structure_has_none():
    q = {"structured": {"options": OPTIONS}, "answer": {"kind": "letters", "letters": "B"}}
    assert grade_structured(q, choice=["B"])["correct"] is True
    assert grade_structured(q, choice=["C"])["correct"] is False


def test_stored_answer_may_arrive_as_json_text():
    q = {"answer": json.dumps({"kind": "integer", "value": 7})}
    assert stored_answer(q) == {"kind": "integer", "value": 7}
    assert grade_structured(q, value="7")["correct"] is True


def test_the_stored_answer_beats_the_structured_copy():
    q = {"answer": {"kind": "letters", "letters": "D"},
         "structured": {"answer": {"kind": "letters", "letters": "A"}}}
    assert grade_structured(q, choice=["D"])["correct"] is True


def test_numeric_source_answers_are_gradable():
    from local_ingest.corpus import numeric_answer

    exact = numeric_answer({"is_range": False, "exact_value": 3, "min_value": None, "max_value": None})
    assert exact == {"kind": "integer", "value": 3.0}
    assert grade_structured({"answer": exact}, value="3")["correct"] is True
    assert grade_structured({"answer": exact}, value="4")["correct"] is False

    rng = numeric_answer({"is_range": True, "exact_value": None, "min_value": 1.45, "max_value": 1.52})
    assert rng == {"kind": "range", "low": 1.45, "high": 1.52}
    assert grade_structured({"answer": rng}, value="1.5")["correct"] is True
    assert grade_structured({"answer": rng}, value="1.6")["correct"] is False

    assert numeric_answer(2.5) == {"kind": "integer", "value": 2.5}
    assert numeric_answer(None) is None
