"""A request for a JEE Advanced question names the topic and prefers an official paper."""

import numpy as np

from retrieval.resolver import _contained_name
from retrieval.search import _official_first
from retrieval.service import _exam_in_query
from retrieval.gate import infer_content_type


def _lex():
    concepts = [
        {
            "concept_id": "ucm",
            "name": "centripetal acceleration",
            "aliases": '["Uniform and Nonuniform Circular Motion"]',
            "vec_row": 0,
        },
        {
            "concept_id": "compare",
            "name": "Comparing Simple Harmonic Motion and Circular Motion",
            "aliases": "[]",
            "vec_row": 1,
        },
        {"concept_id": "shm", "name": "Simple harmonic motion", "aliases": "[]", "vec_row": 2},
    ]
    return {"concepts": concepts}


def test_circular_motion_names_uniform_circular_motion_not_shm():
    sims = np.asarray([0.2, 0.1, 0.9], dtype=np.float32)  # SHM is closer by meaning
    cid = _contained_name("Give me a JEE Advanced question on circular motion", _lex(), sims)
    assert cid == "ucm"


def test_advanced_question_is_a_pyq_for_that_exam():
    query = "Give me a JEE Advanced question on circular motion"
    assert infer_content_type(query) == "pyq"
    assert _exam_in_query(query) == "JEE_ADVANCED"
    assert _exam_in_query("a JEE Main question on friction") == "JEE_MAINS"
    assert _exam_in_query("explain torque") == ""


def test_official_paper_beats_a_coaching_paper_on_the_same_concept():
    rows = [
        {"concept_id": "ucm", "license": "coaching test paper"},
        {"concept_id": "ucm", "license": "official exam paper (jeeadv.ac.in) with official final answer key"},
        {"concept_id": "shm", "license": "official exam paper (jeeadv.ac.in) with official final answer key"},
    ]
    kept, scores = _official_first(rows, [0, 1, 2], [0.9, 0.4, 0.8], concept_id="ucm")
    assert kept == [1]
    assert scores == [0.4]


def test_coaching_stays_when_the_concept_has_no_official_paper():
    rows = [
        {"concept_id": "ucm", "license": "coaching test paper"},
        {"concept_id": "shm", "license": "past exam paper"},
    ]
    kept, _scores = _official_first(rows, [0, 1], [0.9, 0.8], concept_id="ucm")
    assert kept == [0, 1]
