"""H.C. Verma chapters read from PDF pages (local_ingest/hcverma_pdf): what the page reader
returns is kept only when the book's text layer backs it. No network, no model."""

from __future__ import annotations

from pathlib import Path

from local_ingest import hcverma_pdf
from local_ingest.extract.report import Report

PAGE_TEXT = """CHAPTER 39
ALTERNATING CURRENT
39.1 ALTERNATING CURRENT
When a resistor is connected across the terminals of a battery, a current is established in
the circuit. If the direction of the current in a resistor changes alternately, the current is
called an alternating current. Such a current is given by i = i0 sin(wt + phi).
Worked Out Examples
1. An alternating current having peak value 14 A is used to heat a metal wire. To produce the
same rate of heat, find the constant current.
Solution : The constant current equals the rms value, i0 divided by root two, which gives 10 A.
2. Find the current in the circuit shown in figure (39-W1).
Solution : The reactance of the inductor gives the current as computed from the circuit.
"""

READ = {"_page": 1, "blocks": [
    {"kind": "heading", "number": "39.1", "title": "ALTERNATING CURRENT"},
    {"kind": "text", "text": "When a resistor is connected across the terminals of a battery, a current is established "
                             "in the circuit. If the direction of the current in a resistor changes alternately, the "
                             "current is called an alternating current. Such a current is given by $i = i_0 \\sin(\\omega t + \\phi)$."},
    {"kind": "example", "number": "1", "question": "An alternating current having peak value 14 A is used to heat a metal "
     "wire. To produce the same rate of heat, find the constant current.",
     "solution": "The constant current equals the rms value, $i_0/\\sqrt{2}$, which gives 10 A.", "continues_on_next_page": False},
    {"kind": "example", "number": "2", "question": "Find the current in the circuit shown in figure (39-W1).",
     "solution": "The reactance of the inductor gives the current as computed from the circuit.", "continues_on_next_page": False},
]}


def test_records_are_kept_only_when_the_book_backs_them():
    report = Report()
    records = hcverma_pdf.chapter_records(39, [(Path("ac/pdf1.pdf"), [READ], {1: PAGE_TEXT})], report)
    by_id = {r["id"]: r for r in records}
    assert by_id["HCV-C39-SE-001"]["solution_status"] == "book"
    assert by_id["HCV-C39-SE-001"]["chapter_id"] == "alternating_current_circuits"
    assert any(r["kind"] == "theory" and r["question"].startswith("39.1 ALTERNATING CURRENT") for r in records)
    # Example 2 cites a figure nobody described: a student could not solve it.
    assert "HCV-C39-SE-002" not in by_id
    assert any(d["ref"] == "example 2" and "figure" in d["reason"] for d in report.dropped)


def test_an_invented_block_is_set_aside():
    invented = {"_page": 1, "blocks": [{"kind": "text", "text": "Quantum chromodynamics describes gluons binding "
                                        "quarks inside hadrons through colour charge exchange processes everywhere."}]}
    report = Report()
    records = hcverma_pdf.chapter_records(39, [(Path("ac/pdf1.pdf"), [invented], {1: PAGE_TEXT})], report)
    assert not records
    assert any("not read from the page" in d["reason"] for d in report.dropped)
