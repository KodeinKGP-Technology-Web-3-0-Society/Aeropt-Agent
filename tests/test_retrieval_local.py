"""Behaviour tests for the local retrieval pipeline.

These assert the four properties the pipeline exists to guarantee:

  1. an off-syllabus question is never given course material (no hallucinated ground);
  2. every served chunk can be cited back to a source;
  3. a question is never served to the same student twice;
  4. turns that need no retrieval do not retrieve.

They run against the retrieval Postgres (RETRIEVAL_DATABASE_URL, filled by `make push`)
and skip when it is not configured. Point it at the local Supabase, not the deployed one,
for CI. The novelty ledger is kept in memory here (AEROPT_LEDGER=memory, tests/conftest.py)
so test learners are never written to the user database.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse
import pytest

from retrieval import gate, store

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

def _is_local_db():
    if not store.index_exists():
        return False
    host = (urlparse(store.database_url()).hostname or "").lower()
    return host in LOCAL_HOSTS or os.getenv("ALLOW_HOSTED_RETRIEVAL_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not _is_local_db(),
    reason="retrieval database not configured on localhost (RETRIEVAL_DATABASE_URL + make push; or set ALLOW_HOSTED_RETRIEVAL_TESTS=1)",
)


def _one(sql: str, params=None) -> dict:
    rows = store.query(sql, params)
    return rows[0] if rows else None

IN_SYLLABUS = [
    "what is the work energy theorem",
    "explain rolling without slipping on an incline",
    "moment of inertia of a uniform rod about its end",
    "why is centrifugal force called a pseudo force",
    "young double slit fringe width derivation",
    "stopping potential in the photoelectric effect",
    "emf induced in a coil rotating in a magnetic field",
]

OFF_SYLLABUS = [
    "who won the cricket world cup",
    "best pizza in delhi",
    "what is the capital of france",
    "tell me a joke",
]


# ── 1. grounding honesty ──────────────────────────────────────────────────────
def test_in_syllabus_queries_resolve_and_ground():
    from retrieval import service

    for query in IN_SYLLABUS:
        pack = service.retrieve(query, user_id="", mark=False)
        assert pack["confidence"] == "ok", (query, pack["confidence"], pack.get("concept"))
        assert pack["chunks"], query
        assert pack["concept"]["in_scope"] is True


def test_off_syllabus_queries_stay_unresolved():
    from retrieval import service

    for query in OFF_SYLLABUS:
        pack = service.retrieve(query, user_id="", mark=False)
        assert pack["confidence"] in {"unresolved", "skipped"}, (query, pack["confidence"])
        assert not pack["chunks"], query
        if pack["confidence"] == "unresolved":
            assert any("outside" in n or "Nothing in the syllabus" in n for n in pack["notes"])


def test_weak_grounding_is_labelled_not_presented_as_course_material():
    """A subject the corpus does not cover (maths) may resolve weakly; it must not come back as confident."""
    from retrieval import service

    pack = service.retrieve("integrate x squared by parts", user_id="", mark=False)
    assert pack["confidence"] != "ok" or pack["notes"]


def test_chemistry_is_answered_from_chemistry():
    """Chemistry (NCERT) and physics share one corpus; a chemistry question gets chemistry material,
    and nothing is ever linked to a concept of the other subject."""
    from retrieval import service

    pack = service.retrieve("explain aufbau principle and hund's rule", user_id="", mark=False)
    assert pack["chunks"] and all(c["subject"] == "Chemistry" for c in pack["chunks"])
    crossed = _one(
        "select count(*) as n from public.chunk_concepts cc join public.content c using (content_id)"
        " join public.concepts k on k.concept_id = cc.concept_id where k.subject <> c.subject"
    )["n"]
    assert crossed == 0


# ── 2. citations ──────────────────────────────────────────────────────────────
def test_every_chunk_carries_a_citation():
    from retrieval import service

    pack = service.retrieve("derive bernoulli's equation", user_id="", mark=False)
    assert pack["chunks"]
    for chunk in pack["chunks"]:
        assert chunk["citation"].strip(), chunk["content_id"]
        assert chunk["source_book"].strip()


def test_out_of_jee_syllabus_content_is_flagged():
    rows = _one("select count(*) as n from public.content where not coalesce(in_syllabus, true)")
    assert rows["n"] > 0, "nothing marked out of syllabus — the scope map is not being applied"


# ── 3. never the same question twice ──────────────────────────────────────────
def test_practice_questions_never_repeat_for_one_student():
    from retrieval import service

    user = "test-novelty-user"
    service.forget(user)
    seen: set[str] = set()
    for _ in range(6):
        pack = service.retrieve(
            "give me a practice question on friction",
            user_id=user,
            content_type="practice",
            top_k=1,
        )
        if not pack["chunks"]:
            break
        cid = pack["chunks"][0]["content_id"]
        assert cid not in seen, f"served {cid} twice"
        seen.add(cid)
    service.forget(user)
    assert len(seen) >= 4


def test_near_duplicate_questions_are_filtered_by_fingerprint():
    from retrieval import service

    user = "test-fingerprint-user"
    service.forget(user)
    first = service.retrieve(
        "practice question on kinetic energy", user_id=user, content_type="practice", top_k=1
    )
    assert first["chunks"]
    fingerprint = first["chunks"][0]["fingerprint"]
    assert fingerprint in store.served_fingerprints(user)
    later = service.retrieve(
        "another kinetic energy question", user_id=user, content_type="practice", top_k=3
    )
    assert fingerprint not in {c["fingerprint"] for c in later["chunks"]}
    service.forget(user)


def test_practice_batch_returns_only_unseen_questions():
    from retrieval import service

    user = "test-batch-user"
    service.forget(user)
    concept = _one(
        "select primary_concept_id as concept_id from public.content where is_question and has_solution"
        " group by 1 order by count(*) desc limit 1"
    )["concept_id"]
    first = service.practice_batch(user_id=user, concept_id=concept, count=3)
    second = service.practice_batch(user_id=user, concept_id=concept, count=3)
    overlap = {q["content_id"] for q in first["questions"]} & {
        q["content_id"] for q in second["questions"]
    }
    service.forget(user)
    assert first["questions"]
    assert not overlap, overlap


def test_draining_a_concept_is_declared_not_hidden():
    """Once a concept has no unseen questions left, saying so is the honest move.

    Search legitimately widens to the chapter to stay relevant, so the test asserts
    what the student is told, not which rows came back.
    """
    from retrieval import service

    user = "test-exhausted-user"
    service.forget(user)
    concept = _one(
        "select primary_concept_id as concept_id from public.content where is_question"
        " group by 1 having count(*) between 2 and 6 order by 1 limit 1"
    )["concept_id"]
    declared = False
    for _ in range(14):
        pack = service.retrieve(
            "one more question", user_id=user, concept_id=concept, content_type="practice", top_k=2
        )
        novelty = pack["novelty"]
        if novelty["concept_pool_exhausted"]:
            declared = True
            assert novelty["concept_unseen_remaining"] == 0
            if novelty["borrowed_from_neighbours"] or novelty["pool_exhausted"]:
                assert pack["notes"], "a drained concept must be declared, not hidden"
            break
    service.forget(user)
    assert declared, "concept pool never reported as drained"


# ── 4. no retrieval when none is needed ───────────────────────────────────────
@pytest.mark.parametrize(
    "query,expected",
    [
        ("hi", "skip"),
        ("hi there", "skip"),
        ("thanks, that helped!", "skip"),
        ("ok got it", "skip"),
        ("what is 45*3", "skip"),
        ("calculate 12.5 / 4", "skip"),
        ("explain that again but shorter", "reuse"),
        ("why was my answer wrong?", "reuse"),
        ("and this one?", "reuse"),
        ("give me another question", "retrieve"),
        ("what about friction on an incline", "retrieve"),
        ("derive the work energy theorem", "retrieve"),
    ],
)
def test_gate_decisions(query, expected):
    decision = gate.decide(
        query,
        state={"retrieved_chunks": [{"content_id": "x"}]},
        recent_turns=[{"role": "user", "content": "previous"}],
    )
    assert decision.action == expected, (query, decision.action, decision.reason)


def test_answer_submission_is_not_a_new_question():
    for answer in ["(b)", "option 3", "9.8 m/s^2", "42"]:
        decision = gate.decide(answer, state={"awaiting_graded_response": True})
        assert decision.action == "skip", answer
        assert decision.notes.get("grading_turn")


def test_skipped_turn_returns_no_material():
    from retrieval import service

    pack = service.retrieve("thanks!", user_id="", state={"retrieved_chunks": [{"a": 1}]})
    assert pack["confidence"] == "skipped"
    assert pack["chunks"] == []


def test_forced_retrieval_overrides_the_gate():
    from retrieval import service

    pack = service.retrieve(
        "thanks!", user_id="", concept_id="", forced=True, mark=False
    )
    assert pack["decision"]["action"] == "retrieve"


def test_reuse_downgrades_to_retrieve_when_the_topic_moves():
    """The gate cannot see the syllabus; the service must re-check the concept."""
    from retrieval import service

    state = {
        "primary_concept_id": "moment_of_inertia",
        "retrieved_chunks": [{"content_id": "x", "content_type": "Theory"}],
    }
    pack = service.retrieve("and this one?", user_id="", state=state, mark=False)
    assert pack["confidence"] in {"reused", "ok"}
    moved = service.retrieve(
        "what about the photoelectric effect", user_id="", state=state, mark=False
    )
    assert moved["confidence"] == "ok"
    assert moved["concept"]["concept_id"] != "moment_of_inertia"


# ── index integrity ───────────────────────────────────────────────────────────
def test_index_is_internally_consistent():
    meta = store.meta()
    chunks = _one("select count(*) as n from public.content")["n"]
    assert chunks == meta["content_rows"]
    assert _one("select count(*) as n from public.content where embedding is null")["n"] == 0
    assert store.concept_vectors().shape[0] == len(store.all_concepts())
    orphans = _one(
        "select count(*) as n from public.content c left join public.concepts k on k.concept_id = c.primary_concept_id"
        " where k.concept_id is null"
    )["n"]
    assert orphans == 0, f"{orphans} chunks point at a concept that does not exist"
    dangling = _one(
        "select count(*) as n from public.concept_edges e left join public.concepts k on k.concept_id = e.requires"
        " where k.concept_id is null"
    )["n"]
    assert dangling == 0


def test_prerequisite_graph_is_acyclic():
    edges = store.query("select concept_id, requires from public.concept_edges")
    graph: dict[str, list[str]] = {}
    for row in edges:
        graph.setdefault(row["concept_id"], []).append(row["requires"])
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        assert state.get(node) != 1, f"cycle through {node}"
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep)
        state[node] = 2

    for node in list(graph):
        visit(node)


def test_every_in_syllabus_concept_has_material():
    """Every textbook concept has content. JEE syllabus topics the textbook never
    teaches (local_ingest/pg_syllabus_topics.py: lab experiments, logic gates) are
    the one allowed exception, and only when recorded as such."""
    bare = _one(
        """
        select count(*) as n from public.concepts k
        where k.in_syllabus and k.source <> 'syllabus'
          and not exists (select 1 from public.chunk_concepts cc where cc.concept_id = k.concept_id)
        """
    )["n"]
    assert bare == 0, f"{bare} in-syllabus concepts have no content at all"
    unrecorded = _one(
        """
        select count(*) as n from public.concepts k
        where k.source = 'syllabus'
          and not exists (select 1 from public.chunk_concepts cc where cc.concept_id = k.concept_id)
          and not exists (select 1 from public.syllabus_topics t where t.concept_id = k.concept_id and t.material < 3)
        """
    )["n"]
    assert unrecorded == 0, f"{unrecorded} syllabus topics have no material and are not marked tutor-taught"


def test_syllabus_topics_cover_the_jee_main_units():
    """All 20 JEE Main units (NTA 2026-27) are present, each topic resolvable."""
    from local_ingest.jee_topics import JEE_TOPICS

    units = list(dict.fromkeys(t["unit"] for t in store.syllabus_topics("Physics")))
    assert units == [u for u, _ in JEE_TOPICS]
    missing = _one(
        "select count(*) as n from public.syllabus_topics t left join public.concepts k on k.concept_id = t.concept_id"
        " where k.concept_id is null"
    )["n"]
    assert missing == 0


def test_retrieval_is_fast():
    """The local backend exists for latency; a regression here is a real regression."""
    import time
    from urllib.parse import urlparse

    from retrieval import service, store

    host = (urlparse(store.database_url()).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip(f"latency budget (<150ms) applies to local database, not hosted ({host})")

    service.retrieve("warm up the caches", user_id="", mark=False)
    started = time.perf_counter()
    for query in IN_SYLLABUS:
        service.retrieve(query, user_id="", mark=False)
    per_query_ms = (time.perf_counter() - started) / len(IN_SYLLABUS) * 1000
    assert per_query_ms < 150, f"{per_query_ms:.0f} ms per query"


# ── the Neo4j-shaped shim the other call sites use ────────────────────────────
def test_local_graph_client_answers_the_neo4j_surface():
    from retrieval.local_graph import graph

    concept = _one("select concept_id from public.concept_edges order by 1 limit 1")["concept_id"]
    assert graph.ping()
    assert graph.get_prerequisite_concepts(concept)
    assert graph.get_content_ids_for_concept(concept, content_type="theory", limit=3)
    chain = graph.get_requires_chain_for_quiz(concept)
    assert chain == sorted(chain, key=lambda r: -r["depth"]), "quiz chain must be leaf-first"
    hits = graph.vector_search(query_text="torque on a rigid body", top_k=3)
    assert hits and hits[0]["score"] > hits[-1]["score"]


def test_local_graph_client_refuses_foreign_vectors():
    """A 3072-d (Gemini-sized) vector against the 256-d corpus must fail loudly, not rank noise."""
    from retrieval import embedder
    from retrieval.local_graph import graph

    with pytest.raises(embedder.EmbeddingMismatch, match="256-d"):
        graph.vector_search(query_vector=[0.1] * 3072, top_k=3)


def test_follow_up_candidates_exclude_seen_ids():
    from retrieval.local_graph import graph

    concept = _one(
        "select concept_id from public.chunk_concepts group by 1 having count(*) > 4 order by 1 limit 1"
    )["concept_id"]
    first = graph.get_content_ids_for_follow_up(concept, seen_ids=[], difficulty_max=5, limit=4)
    assert first
    later = graph.get_content_ids_for_follow_up(
        concept, seen_ids=first, difficulty_max=5, limit=4
    )
    assert not set(first) & set(later)


# ── exam level ────────────────────────────────────────────────────────────────
def test_pyq_retrieval_respects_the_exam():
    from retrieval import service

    for exam, expected in (("JEE_MAINS", "Jee main"), ("JEE_ADVANCED", "Jee Advanced")):
        pack = service.retrieve(
            "pyq on current electricity", user_id="", content_type="pyq",
            target_exam=exam, mark=False, top_k=5,
        )
        assert pack["chunks"], exam
        assert {c["target_exam"] for c in pack["chunks"]} == {expected}, exam


def test_advanced_questions_are_pitched_higher_than_main():
    """Most questions are labelled for both exams, so only the band separates them."""
    from retrieval import service

    queries = [
        "practice question on rotational motion",
        "practice on friction",
        "questions on electrostatics",
        "practice on thermodynamics",
    ]
    levels: dict[str, list[int]] = {}
    for exam in ("JEE_MAINS", "JEE_ADVANCED"):
        found: list[int] = []
        for query in queries:
            pack = service.retrieve(
                query, user_id="", content_type="practice", target_exam=exam, mark=False, top_k=5
            )
            found += [c["difficulty"] for c in pack["chunks"] if c["difficulty"]]
        levels[exam] = found
        assert found, exam
    main = sum(levels["JEE_MAINS"]) / len(levels["JEE_MAINS"])
    adv = sum(levels["JEE_ADVANCED"]) / len(levels["JEE_ADVANCED"])
    assert adv > main, f"Advanced {adv:.2f} should outrank Main {main:.2f}"
    assert min(levels["JEE_ADVANCED"]) >= 3, "an Advanced request should not serve the easiest tier"


# ── 5. material the tutor cannot answer from is not served ────────────────────
def test_no_syllabus_topic_is_left_without_material():
    """Every JEE topic has at least one passage, so the tutor never teaches one
    from recall alone (local_ingest/reference_notes.py covers what no source does)."""
    bare = [t["topic"] for t in store.syllabus_topics() if t["material"] == 0]
    assert bare == []


def test_reference_notes_render():
    from utils.latex_guidelines import find_latex_issues

    rows = store.query(
        "select content_id, core_text from public.content where source_book = 'Aeropt JEE reference notes'"
    )
    assert rows
    broken = {r["content_id"]: find_latex_issues(r["core_text"]) for r in rows}
    assert not {k: v for k, v in broken.items() if v}


def test_questions_whose_figure_is_missing_are_not_served():
    """A question that says 'the following circuit' with no circuit makes the model
    invent one. Such questions are dropped at ingest, and solutions that lost their
    working to images are cut back to the answer key."""
    leaked = _one(
        "select count(*) as n from public.content where is_question and "
        "(core_text like '%%[IMAGE]%%' or solution like '%%[IMAGE]%%' "
        " or (license = 'coaching test paper' and solution like '%%= =%%'))", ()
    )["n"]
    assert leaked == 0


def test_textbook_figures_are_described_in_text():
    row = _one(
        "select core_text from public.content where core_text like %s",
        ("%acceleration of the body of mass 10.0 kg shown below%",),
    )
    assert row and "[Figure:" in row["core_text"] and "37 degrees" in row["core_text"]


def test_test_papers_are_not_passed_off_as_official_pyqs():
    from retrieval import service

    mislabelled = _one(
        "select count(*) as n from public.content where license = 'coaching test paper' and heading like %s",
        ("%PYQ%",),
    )["n"]
    assert mislabelled == 0
    pack = service.retrieve("pyq on surface tension", user_id="", content_type="pyq", mark=False)
    if any(c["license"] == "coaching test paper" for c in pack["chunks"]):
        assert any("not verified official papers" in n for n in pack["notes"])


def test_optics_material_carries_the_jee_sign_convention():
    from retrieval import service

    pack = service.retrieve("lens formula for a convex lens", user_id="", content_type="theory", mark=False)
    assert pack["chunks"]
    assert any("1/v - 1/u = 1/f" in n for n in pack["notes"])


def test_a_rejected_one_word_name_falls_through_to_the_next_named_concept():
    from retrieval import resolver

    res = resolver.resolve("half deflection method galvanometer resistance")
    assert res.concept_id == "galvanometer_resistance_and_figure_of_merit"


# ── 6. official papers ─────────────────────────────────────────────────────────
def test_paper_references_are_parsed():
    from retrieval import exam_refs

    assert exam_refs.parse("solve JEE Advanced 2019 paper 1 question 5").content_id == "pyq_jee_adv_2019_p1_phy_q05"
    assert exam_refs.parse("jee adv 2023 p2 q14").content_id == "pyq_jee_adv_2023_p2_phy_q14"
    assert exam_refs.parse("give me a jee advanced pyq on rotation") is None
    assert exam_refs.parse("JEE Main 2019 paper 1 q5") is None


def test_a_named_official_question_comes_back_exactly_or_not_at_all():
    from retrieval import exam_refs, service

    row = _one("select content_id from public.content where content_id like %s order by 1 limit 1", ("pyq_jee_adv_%",))
    if row is None:
        pytest.skip("no official papers ingested (python -m local_ingest.exams)")
    year, paper, q = row["content_id"].split("_")[3], row["content_id"].split("_")[4][1], row["content_id"].split("q")[-1]
    pack = service.retrieve(f"JEE Advanced {year} paper {paper} question {int(q)}", user_id="", mark=False)
    assert [c["content_id"] for c in pack["chunks"]] == [row["content_id"]]
    assert "Official answer" in pack["chunks"][0]["solution"]

    missing = service.retrieve("solve JEE Advanced 2011 paper 1 question 3", user_id="", content_type="pyq", mark=False)
    assert "not in the verified set" in missing["notes"][0] or "could not be found" in missing["notes"][0]
    assert all(c["content_id"] != exam_refs.parse("JEE Advanced 2011 paper 1 q3").content_id for c in missing["chunks"])


def test_year_level_request_parses_without_question_number():
    from retrieval import exam_refs

    ref = exam_refs.parse_paper("give me jee advanced 2016 famous question with diagram")
    assert ref is not None and ref.year == 2016 and ref.paper is None
    assert exam_refs.parse_paper("jee adv 2023 paper 2 problems").id_prefix == "pyq_jee_adv_2023_p2"
    # A named question is the exact-question path, not this one.
    assert exam_refs.parse_paper("jee adv 2023 p2 q14") is None
    assert exam_refs.parse_paper("explain torque") is None
