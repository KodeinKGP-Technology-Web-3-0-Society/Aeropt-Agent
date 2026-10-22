"""Which OpenStax Calculus modules are allowed into the JEE maths chapters."""

from pathlib import Path

from local_ingest.cnxml import parse_module
from local_ingest.corpus import allen_range
from local_ingest.openstax_calculus import DEST, chapter_for


def test_integration_techniques_join_the_integrals_chapter():
    assert chapter_for("Techniques of Integration", "Integration by Parts") == "integrals"
    assert chapter_for("Techniques of Integration", "Partial Fractions") == "integrals"
    assert chapter_for("Integration", "The Fundamental Theorem of Calculus") == "integrals"


def test_series_and_first_order_equations_keep_their_chapters():
    assert chapter_for("Sequences and Series", "Sequences") == "sequences_and_series"
    assert chapter_for("Introduction to Differential Equations", "Separable Equations") == "differential_equations"


def test_out_of_syllabus_calculus_is_left_out():
    assert chapter_for("Parametric Equations and Polar Coordinates", "Polar Coordinates") is None
    assert chapter_for("Applications of Integration", "Volumes of Revolution") is None
    assert chapter_for("Techniques of Integration", "Numerical Integration") is None
    assert chapter_for("Introduction to Differential Equations", "Second-Order Linear Equations") is None
    assert chapter_for("Vectors in Space", "The Dot Product") is None
    assert chapter_for("Sequences and Series", "Alternating Series") is None
    assert chapter_for("Power Series", "Taylor and Maclaurin Series") is None
    assert chapter_for("Introduction to Differential Equations", "The Logistic Equation") is None
    assert chapter_for("Limits", "The Precise Definition of a Limit") is None
    assert chapter_for("Applications of Derivatives", "Related Rates") is None


def test_allen_pages_follow_the_printed_contents():
    assert allen_range(1)[1] == "mole_concept_stoichiometry"
    assert allen_range(39)[1] == "chemical_kinetics"
    assert allen_range(58)[1] == "surface_chemistry"
    assert allen_range(86)[1] == "p_block_elements"
    assert allen_range(118)[1] == "qualitative_analysis"
    assert allen_range(180)[1] == "biomolecules_and_polymers"


def test_worked_example_keeps_the_solution():
    path = DEST / "modules" / "m53656" / "index.cnxml"
    if not path.exists():
        return
    mod = parse_module(path)
    solved = [ex for ex in mod.examples if len(ex["solution"]) > 80]
    assert solved, "integration by parts examples lost their solutions"
    assert "cos" in solved[0]["solution"]
    assert len(solved[0]["question"]) > 20
