"""Linked JEE Advanced papers for a concept: shape only, no database."""

from services.concept_pyqs import shape_pyqs

OFFICIAL = "official exam paper (jeeadv.ac.in) with official final answer key"


def _row(**overrides):
    row = {
        "content_id": "pyq_2024_p1_q3",
        "content_type": "PYQ",
        "target_exam": "Jee Advanced",
        "license": OFFICIAL,
        "source_book": "JEE Advanced 2024 Paper 1",
        "heading": "JEE Advanced 2024 Paper 1 · Q3 · one correct option",
        "source_ref": "Q3 (official paper and final answer key)",
        "core_text": "A long stem that should not be used when structured.stem is present.",
        "structured": '{"stem": "A particle moves so that $x = 2t$.", "answer": {"kind": "letters", "letters": "A"}}',
        "role_rank": 0,
    }
    row.update(overrides)
    return row


def test_official_row_keeps_year_paper_and_stem_not_the_key():
    items = shape_pyqs([_row()])
    assert items == [{
        "content_id": "pyq_2024_p1_q3",
        "year": 2024,
        "paper": "1",
        "stem": "A particle moves so that $x = 2t$.",
    }]
    assert "letters" not in items[0]["stem"]


def test_coaching_and_mains_rows_are_dropped():
    items = shape_pyqs([
        _row(content_id="coach", license="coaching test paper", source_book="P-01"),
        _row(content_id="main", target_exam="Jee main", source_book="JEE Main 2021"),
        _row(content_id="both", target_exam="Jee main and Advanced"),
    ])
    assert items == []


def test_missing_year_or_paper_stays_blank():
    items = shape_pyqs([
        _row(
            content_id="no-when",
            source_book="JEE Advanced",
            heading="JEE Advanced · Q1",
            source_ref="Q1",
            structured=None,
            core_text="Find the speed.",
        )
    ])
    assert items[0]["year"] is None
    assert items[0]["paper"] is None
    assert items[0]["stem"] == "Find the speed."


def test_official_paper_outranks_a_newer_digitized_set():
    digitized = "JEE Official Examination Questions (Joint Apex Board / NTA); digitized practice set"
    items = shape_pyqs([
        _row(
            content_id="new-set",
            license=digitized,
            source_book="JEE Official Exam (set)",
            heading="JEE Advanced 2025",
            source_ref="",
            structured=None,
            core_text="Two mirrors face each other.",
            role_rank=0,
        ),
        _row(content_id="paper-2016", source_book="JEE Advanced 2016 Paper 1", heading="JEE Advanced 2016 Paper 1", role_rank=0),
    ])
    assert [item["content_id"] for item in items] == ["paper-2016", "new-set"]
    assert items[0]["paper"] == "1"
    assert items[1]["paper"] is None


def test_primary_and_newer_papers_come_first():
    items = shape_pyqs([
        _row(content_id="old-primary", source_book="JEE Advanced 2014 Paper 2", heading="JEE Advanced 2014 Paper 2", role_rank=0),
        _row(content_id="new-secondary", source_book="JEE Advanced 2024 Paper 1", heading="JEE Advanced 2024 Paper 1", role_rank=1),
        _row(content_id="new-primary", source_book="JEE Advanced 2023 Paper 1", heading="JEE Advanced 2023 Paper 1", role_rank=0),
    ])
    assert [item["content_id"] for item in items] == ["new-primary", "old-primary", "new-secondary"]


def test_preview_does_not_leave_a_dangling_dollar():
    stem = "Before " + ("$x$ " * 40) + "and then $unfinished math that runs on"
    items = shape_pyqs([_row(structured=None, core_text=stem, heading="", source_book="JEE Advanced 2019 Paper 1")])
    assert items[0]["stem"].endswith("…")
    assert items[0]["stem"].count("$") % 2 == 0
