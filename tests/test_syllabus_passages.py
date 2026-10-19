"""Syllabus topics must attach the passage that teaches the technique, not only the heading."""

from local_ingest.pg_syllabus_topics import proximity_query
from local_ingest.reference_notes import NOTES
from local_ingest.syllabus import Chapter, Concept
from retrieval.resolver import _prefer_grounded
from retrieval.search import _PASSAGE_SKIP
from utils.latex_guidelines import find_latex_issues


def test_proximity_query_skips_stop_words():
    assert proximity_query("modulus of complex number") == "modulus & complex & number"
    assert proximity_query("King's property") == "Kings & property"


def test_retrieval_skips_exercise_banners_orphan_parts_and_handbook_furniture():
    assert "content_type = 'PYQ' and length(c.core_text) < 120" in _PASSAGE_SKIP
    assert "ALLEN%%" in _PASSAGE_SKIP
    assert "exercise" in _PASSAGE_SKIP


def test_linked_concept_still_receives_phrase_passages():
    """A section titled 'Methods of integration' must also receive the substitution passage."""
    from local_ingest import pg_syllabus_topics as topics

    class Cur:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            if "fts" in self.sql:
                return [("sub_1", "integrals")]
            return []

    concept = Concept(
        concept_id="math_methods_of_integration", name="Methods of Integration", definition="",
        chapter_id="integrals", chapter="Integrals", unit="Integral Calculus", in_syllabus=True,
        module_id="", order=1, subject="Mathematics",
    )
    chapter = Chapter(
        chapter_id="integrals", title="Integrals", volume="", unit="Integral Calculus",
        jee_unit="Integral Calculus", in_syllabus=True, order=1, subject="Mathematics",
    )
    res = topics.apply(
        Cur(), [concept], [chapter],
        [{"content_id": "menu", "concept_id": concept.concept_id, "secondary_concepts": []}],
        lambda texts: (_ for _ in ()).throw(AssertionError("no new concept to embed")),
        subject="Mathematics",
        topic_list=[("Integral Calculus", [("Methods of integration", "integration by substitution")])],
        verbose=False,
    )
    assert ("sub_1", concept.concept_id) in res["links"]
    assert res["stats"]["linked"] == 1


def test_empty_glossary_does_not_beat_a_concept_with_passages():
    candidates = [
        {"concept_id": "baryon", "score": 0.50},
        {"concept_id": "math_properties_of_definite_integrals", "score": 0.46},
    ]
    chosen = _prefer_grounded(candidates, {"math_properties_of_definite_integrals"})
    assert chosen["concept_id"] == "math_properties_of_definite_integrals"
    # A clearly closer empty concept is left alone; the caller still marks it weak.
    assert _prefer_grounded(candidates, set())["concept_id"] == "baryon"


def test_named_jee_tricks_have_a_reference_note():
    titles = {title for _chapter, title, _text in NOTES}
    required = {
        "Properties of definite integrals",
        "Integrals of special forms",
        "Middle term and greatest term",
        "Special series and sum to n terms",
        "Complex numbers and algebraic operations",
        "Modulus, argument and conjugate of complex numbers",
        "Formation and transformation of quadratic equations",
        "Tangents to parabola",
        "Summation of series involving binomial coefficients",
        "Position, isotopes and preparation of hydrogen",
        "Drugs and their therapeutic action",
    }
    assert required <= titles
    broken = {
        title: find_latex_issues(text)
        for _chapter, title, text in NOTES
        if title in required and find_latex_issues(text)
    }
    assert broken == {}
