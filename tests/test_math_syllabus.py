"""Tests for Class 11 & 12 Mathematics concept and syllabus extraction."""

from __future__ import annotations

from local_ingest import jee_syllabus, jee_topics, ncert_syllabus, syllabus
from local_ingest.ncert import catalog


def test_mathematics_catalog():
    math_chs = catalog.MATHEMATICS_CHAPTERS
    assert len(math_chs) == 27
    codes = [c.code for c in math_chs]
    assert len(set(codes)) == 27

    # 14 Class 11 chapters
    class11 = [c for c in math_chs if c.code.startswith("kemh")]
    assert len(class11) == 14
    assert class11[0].code == "kemh101"
    assert class11[0].title == "Sets"
    assert class11[0].book == "NCERT Mathematics Class XI"

    # 13 Class 12 chapters (6 in Part 1, 7 in Part 2)
    class12_p1 = [c for c in math_chs if c.code.startswith("lemh1")]
    class12_p2 = [c for c in math_chs if c.code.startswith("lemh2")]
    assert len(class12_p1) == 6
    assert len(class12_p2) == 7
    assert class12_p1[0].book == "NCERT Mathematics Class XII Part I"
    assert class12_p2[0].book == "NCERT Mathematics Class XII Part II"

    for c in math_chs:
        assert c.subject == "Mathematics"
        assert c.chapter_id
        assert c.unit


def test_jee_mathematics_topics():
    topics = jee_topics.get_topics("Mathematics")
    assert len(topics) >= 16, f"Expected >= 16 JEE Main math units, got {len(topics)}"
    total_topics = sum(len(ts) for _, ts in topics)
    assert total_topics >= 80, f"Expected at least 80 topics, got {total_topics}"

    unit_names = [u for u, _ in topics]
    assert "Sets, Relations and Functions" in unit_names
    assert "Complex Numbers and Quadratic Equations" in unit_names
    assert "Matrices and Determinants" in unit_names
    assert "Limit, Continuity and Differentiability" in unit_names
    assert "Integral Calculus" in unit_names
    assert "Coordinate Geometry - Straight Lines" in unit_names
    assert "Vector Algebra" in unit_names
    assert "Statistics and Probability" in unit_names
    assert "Trigonometry" in unit_names


def test_ncert_math_concept_extraction():
    data = ncert_syllabus.build()
    math_concepts = [c for c in data["concepts"] if c.subject == "Mathematics"]
    math_chapters = [c for c in data["chapters"] if c.subject == "Mathematics"]

    assert len(math_chapters) == 27
    assert len(math_concepts) >= 200, f"Expected >=200 math concepts, got {len(math_concepts)}"

    # All concept IDs must be unique and prefixed with math_
    cids = [c.concept_id for c in math_concepts]
    assert len(cids) == len(set(cids)), "Duplicate concept IDs found!"
    assert all(cid.startswith("math_") for cid in cids)

    # Definitions must be non-empty and sensible
    for c in math_concepts:
        assert len(c.definition) > 10
        assert c.name
        assert c.chapter_id

    # Verify key canonical concepts exist
    cnames = {c.name.lower() for c in math_concepts}
    assert any("sets and their representations" in name for name in cnames)
    assert any("complex numbers" in name for name in cnames)
    assert any("matrix" in name for name in cnames)
    assert any("continuity" in name for name in cnames)
    assert any("methods of integration" in name for name in cnames)
    assert any("types of vectors" in name for name in cnames)


def test_math_prerequisite_graph_acyclic():
    data = ncert_syllabus.build()
    math_concepts = {c.concept_id for c in data["concepts"] if c.subject == "Mathematics"}
    math_edges = [(e["concept_id"], e["requires"]) for e in data["requires"]
                  if e["concept_id"] in math_concepts and e["requires"] in math_concepts]

    assert len(math_edges) > 50, "Expected rich prerequisite DAG for mathematics"

    # Cycle check via Kahn's algorithm or DFS
    adj: dict[str, list[str]] = {}
    in_degree: dict[str, int] = {cid: 0 for cid in math_concepts}
    for src, dst in math_edges:
        adj.setdefault(dst, []).append(src)
        in_degree[src] = in_degree.get(src, 0) + 1

    queue = [cid for cid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        curr = queue.pop(0)
        visited += 1
        for neighbor in adj.get(curr, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    assert visited == len(math_concepts), f"Cycle detected in Mathematics prerequisite graph! Visited {visited}/{len(math_concepts)}"


def test_full_syllabus_integration():
    data = syllabus.build()
    chapters = data["chapters"]
    concepts = data["concepts"]

    subjects = {c.subject for c in chapters}
    assert subjects == {"Physics", "Chemistry", "Mathematics"}

    concept_subjects = {c.subject for c in concepts}
    assert concept_subjects == {"Physics", "Chemistry", "Mathematics"}

    # Canonical chapters and aliases
    math_canonical = jee_syllabus.canonical_chapters("Mathematics")
    assert len(math_canonical) >= 20
    math_alias = jee_syllabus.alias_index("Mathematics")
    assert "sets" in math_alias or "sets and their representations" in math_alias or "straight lines" in math_alias
