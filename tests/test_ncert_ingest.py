"""NCERT collection (local_ingest/ncert): blocks stitch across pages, and nothing a
student cannot use survives validation. Synthetic pages; no network, no model."""

from __future__ import annotations

from local_ingest.extract.report import Report
from local_ingest.ncert import catalog, chapter

CH = catalog.select({"leph103"})[0]

PAGES = [
    {"_page": 1, "blocks": [
        {"kind": "heading", "number": "3.4", "title": "OHM'S LAW"},
        {"kind": "text", "text": "Imagine a conductor through which a current $I$ is flowing and let $V$ be the potential difference between its ends."},
        {"kind": "figure", "number": "3.2", "caption": "A rectangular slab", "description": "A slab of length $l$ and cross-section $A$."},
        {"kind": "example", "number": "3.1", "question": "A slab of length l and area A as in Fig. 3.2 carries current I; find its resistance.",
         "solution": "", "continues_on_next_page": True},
    ]},
    {"_page": 2, "blocks": [
        {"kind": "continuation", "part": "example_solution", "text": "Using $R = \\rho l / A$ the resistance follows directly from the dimensions."},
        {"kind": "text", "text": "Ohm's law states that $V = RI$, where the constant $R$ is the resistance of the conductor."},
        {"kind": "box", "text": "Georg Simon Ohm was a German physicist.", "biography": True},
        {"kind": "exercise", "number": "3.1", "text": "The storage battery of a car has an emf of 12 V; find the maximum current it can draw."},
        {"kind": "exercise", "number": "3.2", "text": "Find the equivalent resistance of the network shown in Fig. 3.30 between A and B."},
    ]},
]


def test_blocks_stitch_across_pages():
    s = chapter.stitch(PAGES, CH.title)
    assert [x["title"] for x in s["sections"]] == ["OHM'S LAW"]
    ohm = s["sections"][0]
    assert any("V = RI" in p for p in ohm["parts"])
    assert not any("German physicist" in p for p in ohm["parts"]), "biographies are not theory"
    example = s["examples"][0]
    assert example["solution"].startswith("Using $R"), "the solution continued onto page 2"
    assert example["pages"] == [1, 2]
    assert set(s["figures"]) == {"3.2"}


def test_validation_keeps_usable_records_and_reports_the_rest():
    report = Report()
    records = chapter.validate(CH, chapter.stitch(PAGES, CH.title), report, CH.code)
    kinds = sorted((r["kind"], r.get("number", "")) for r in records)
    assert kinds == [("example", "3.1"), ("exercise", "3.1"), ("theory", "")]
    example = next(r for r in records if r["kind"] == "example")
    assert "[Figure 3.2: A rectangular slab" in example["question"], "the figure it cites travels with it"
    dropped = {d["ref"]: d["reason"] for d in report.dropped}
    assert "Fig. 3.30" in dropped["exercise 3.2"], "a figure nobody described cannot be sent to a student"


def test_an_example_without_its_solution_is_dropped():
    pages = [{"_page": 1, "blocks": [
        {"kind": "heading", "number": "3.4", "title": "OHM'S LAW"},
        {"kind": "example", "number": "3.9", "question": "A wire of resistance 4 ohm is stretched to twice its length; find the new resistance.", "solution": ""},
    ]}]
    report = Report()
    records = chapter.validate(CH, chapter.stitch(pages, CH.title), report, CH.code)
    assert not [r for r in records if r["kind"] == "example"]
    assert any("without its solution" in d["reason"] for d in report.dropped)


def test_catalog_covers_both_classes():
    codes = [c.code for c in catalog.CHAPTERS]
    assert len(codes) == 28 and len(set(codes)) == 28
    assert CH.book == "NCERT Physics Class XII Part I"


def test_a_block_not_printed_on_its_page_is_rejected():
    """The failure seen in the full run: a page of Work, Energy and Power transcribed as
    Current Electricity, with a heading copied from the prompt's own example. The page's
    text layer has none of those words, so the blocks are rejected, not stitched in."""
    page_text = ("WORK, ENERGY AND POWER 75 object can do by the virtue of its motion. This notion has been "
                 "intuitively known for a long time. The kinetic energy of a fast flowing stream has been used "
                 "to grind corn. Sailing ships employ the kinetic energy of the wind. A constant force is rare. "
                 "It is the variable force, which is more commonly encountered. 5.5 WORK DONE BY A VARIABLE FORCE")
    pages = [{"_page": 6, "blocks": [
        {"kind": "heading", "number": "5.5", "title": "WORK DONE BY A VARIABLE FORCE"},
        {"kind": "text", "text": "A constant force is rare. It is the variable force, which is more commonly encountered."},
        {"kind": "heading", "number": "3.4", "title": "OHM'S LAW"},
        {"kind": "text", "text": "Georg Simon Ohm established the law experimentally using wires of different "
                                 "resistances; the resistance of a conductor depends on its length and area."},
    ]}]
    s = chapter.stitch(pages, "Work, Energy and Power", {6: page_text})
    assert [x["title"] for x in s["sections"]] == ["WORK DONE BY A VARIABLE FORCE"]
    assert not any("Ohm" in p for x in s["sections"] for p in x["parts"])
    assert len(s["rejected"]) == 2
