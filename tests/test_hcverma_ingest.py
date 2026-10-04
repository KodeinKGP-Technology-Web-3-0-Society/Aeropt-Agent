"""H.C. Verma rows are used only as far as the book confirms them (local_ingest/hcverma.py)."""

from __future__ import annotations

from local_ingest import hcverma


class _Book(hcverma.Book):
    def __init__(self, text: str):  # no PDF: build the same indexes from plain text
        self.flat = text
        self.words_seq = hcverma._seq(text)
        self.phrases = hcverma._shingles(self.words_seq)
        self.words = set(__import__("re").findall(r"[a-z]{4,}", text.lower()))
        self.numbers = set(hcverma._numbers(text))


BOOK = _Book(
    "Example 3 A particle of mass M is placed at the centre of a uniform spherical shell of equal mass M "
    "and radius a. Find the gravitational potential at a point P at a distance a/2 from the centre. "
    "Solution : The potential due to the particle is V1 = - 2GM/a and due to the shell V2 = - GM/a. "
    "The net potential at P is - 3GM/a. Example 4 Calculate the value of acceleration due to gravity at a "
    "point 5.0 km above the earth's surface. Solution : g = 9.80 (1 - 2 x 5.0/6400) = 9.78 m s-2 ."
)


def _row(core: str, solution: str) -> dict:
    return {"content_id": "X", "content_type": "Solved Example", "core_text": core, "solution": solution,
            "diagram_description_core": "", "has_diagram": "false", "page_number": "1", "primary_concept_id": ""}


def test_a_rewritten_solution_to_a_different_problem_is_not_confirmed():
    q = ("A particle of mass M is placed at the centre of a uniform spherical shell of equal mass M and radius a. "
         "Find the gravitational potential at a point P at a distance a/2 from the centre.")
    record, _ = hcverma.verify_row(_row(q, "Treating it as a solid sphere, V = -(GM/2a^3)(3a^2 - r^2), so V = -3GM/2a."), BOOK)
    assert record["kind"] == "exercise" and not record["solution"]


def test_a_rewritten_solution_reaching_the_books_number_is_confirmed():
    q = "Calculate the value of acceleration due to gravity at a point 5.0 km above the earth's surface."
    record, _ = hcverma.verify_row(_row(q, "Using g_h = g(1 - 2h/R): g_h = 9.80 (1 - 10/6400) = 9.785 m/s^2."), BOOK)
    assert record["kind"] == "example" and record["solution_status"].startswith("rewritten")


def test_a_question_not_printed_in_the_book_is_dropped():
    q = ("Describe the characteristics of linearly polarized, circularly polarized and elliptically "
         "polarized light, and explain how each can be produced in the laboratory using filters.")
    record, reasons = hcverma.verify_row(_row(q, "Linearly polarized light has its electric field in one plane."), BOOK)
    assert record is None and "not printed in the book" in reasons[0]
