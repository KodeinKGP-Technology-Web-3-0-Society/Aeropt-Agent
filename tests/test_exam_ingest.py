"""The official-paper pipeline (local_ingest/exams): keys parse exactly, and a question
is joined to an answer only when the join can be proved. No network, no model."""

from __future__ import annotations

from local_ingest.exams import assemble, catalog, keys

KEY_TEXT = """
JEE (Advanced) 2099                                                Paper 1
  Q.1       Let f be a function such that f(x) = x^2. Then which statement is TRUE?
            (A) one (B) two (C) three (D) four
Answer Q1: D
  Q.2       A matrix question about determinants and rank of a matrix.
Answer Q2: A or B
  Q.1       A block of mass m slides down a rough inclined plane of angle theta with friction coefficient mu.
            (A) a (B) b (C) c (D) d
Answer: (A), (C)
  Q.2       A capacitor of capacitance C is charged through a resistor R by a battery of emf E.
           Answer: [1.15 to 1.25]
  Q.3       A uniform rod of length L is pivoted at one end and released from horizontal position.
           Answer: Marks to all
"""

PAPER = catalog.Paper(2099, 1, "paper.pdf", "key.pdf")


def _q(n, stem, options=True, section=1, figure=None):
    return {
        "qno": n, "section": section, "stem": stem, "figure": figure, "pages": [1],
        "options": {k: f"option {k}" for k in "ABCD"} if options else None,
    }


def test_key_formats_normalise():
    assert keys.normalise("(A), (C), (D)").letters == "ACD"
    assert keys.normalise("A, D").letters == "AD"
    r = keys.normalise("[3.9 TO 4.1]")
    assert (r.kind, r.low, r.high) == ("range", 3.9, 4.1)
    assert keys.normalise("[2520]").kind == "integer"
    alt = keys.normalise("[75 to 79] OR [94 to 95]")
    assert alt.kind == "any_of" and [a.low for a in alt.alternatives] == [75.0, 94.0]
    assert keys.normalise("Marks to all").kind == "bonus"


def test_key_blocks_restart_per_subject():
    blocks = keys.parse(KEY_TEXT)
    assert [sorted(b.entries) for b in blocks] == [[1, 2], [1, 2, 3]]
    assert blocks[1].entries[1].letters == "AC"


def test_join_picks_the_physics_block_by_content_and_checks_every_answer():
    questions = {
        1: _q(1, "A block of mass m slides down a rough inclined plane of angle theta with friction coefficient mu."),
        2: _q(2, "A capacitor of capacitance C is charged through a resistor R by a battery of emf E.", options=False, section=2),
        3: _q(3, "A uniform rod of length L is pivoted at one end and released from horizontal position.", options=False, section=2),
    }
    sections = {1: {"answer_type": "multiple_correct"}, 2: {"answer_type": "numeric"}}
    kept, dropped = assemble.join(PAPER, questions, sections, keys.parse(KEY_TEXT))
    assert [k["qno"] for k in kept] == [1, 2]
    assert kept[0]["answer"] == "A, C"
    assert kept[1]["answer"] == "any value from 1.15 to 1.25"
    assert [d["qno"] for d in dropped] == [3]  # bonus question: no gradable answer


def test_a_misnumbered_question_is_not_given_another_questions_answer():
    questions = {
        1: _q(1, "A block of mass m slides down a rough inclined plane of angle theta with friction coefficient mu."),
        # Extraction slipped: this is really the rod question, numbered 2.
        2: _q(2, "A uniform rod of length L is pivoted at one end and released from horizontal position.", options=False, section=2),
    }
    sections = {1: {"answer_type": "multiple_correct"}, 2: {"answer_type": "numeric"}}
    kept, dropped = assemble.join(PAPER, questions, sections, keys.parse(KEY_TEXT))
    assert [k["qno"] for k in kept] == [1]
    assert any(d["qno"] == 2 and "does not match" in d["reason"] for d in dropped)


def test_answer_form_must_fit_the_question_type():
    questions = {1: _q(1, "A block of mass m slides down a rough inclined plane of angle theta with friction coefficient mu.")}
    kept, dropped = assemble.join(PAPER, questions, {1: {"answer_type": "single_correct"}}, keys.parse(KEY_TEXT))
    assert not kept and any(d["qno"] == 1 and "does not fit" in d["reason"] for d in dropped)


def test_a_figure_question_without_a_description_is_dropped():
    stem = "A block of mass m slides down a rough inclined plane of angle theta with friction coefficient mu, as shown in the figure."
    questions = {1: _q(1, stem)}
    sections = {1: {"answer_type": "multiple_correct"}}
    kept, dropped = assemble.join(PAPER, questions, sections, keys.parse(KEY_TEXT))
    assert not kept and any(d["qno"] == 1 and "figure" in d["reason"] for d in dropped)
    questions = {1: _q(1, stem, figure="An incline at angle theta to the horizontal; block m on it; friction mu along the surface.")}
    kept, _ = assemble.join(PAPER, questions, sections, keys.parse(KEY_TEXT))
    assert len(kept) == 1


def test_a_solution_counts_only_if_it_reaches_the_official_answer():
    from local_ingest.exams import solve

    assert solve.matches("A and C", {"answer_raw": "A, C"})
    assert not solve.matches("A, C, D", {"answer_raw": "A, C"})
    assert not solve.matches("ACD", {"answer_raw": "(A), (C)"})
    assert solve.matches("600", {"answer_raw": "[598.00 to 602.00]"})
    assert not solve.matches("603", {"answer_raw": "[598.00 to 602.00]"})
    assert solve.matches("0.75", {"answer_raw": "0.5 OR 0.75"})
    assert not solve.matches("", {"answer_raw": "B"})
