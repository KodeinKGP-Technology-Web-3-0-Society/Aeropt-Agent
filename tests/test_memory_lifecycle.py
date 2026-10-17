"""Lifecycle regressions for grading evidence, feedback links, and embeddings."""

from __future__ import annotations

from agents.memory_agent import build_episode, retrieve_relevant_episodes
from agents.memory_consolidate import eligible_clusters
from db.supabase_client import SupabaseClient
from tests.test_episodic_memory import FakeSupabase, _client, _close, _row, _seed, _stored
from tests.test_memory_agent import _episode, _patch_db, _patch_embed, _state


def test_grade_copies_proficiency_onto_the_episode():
    row = build_episode(
        _state(
            has_graded_outcome=True,
            is_correct=False,
            attempt_id="attempt-1",
            proficiency_before=0.5,
            proficiency_after=0.35,
        ),
        kind="graded",
    )
    assert row is not None
    assert row["proficiency_before"] == 0.5
    assert row["proficiency_after"] == 0.35
    assert row["assessment_status"] == "completed"
    assert row["observed_outcome"] == "incorrect"


def test_ungraded_explanation_stays_unknown():
    row = build_episode(_state(), kind="chat")
    assert row is not None
    assert row["observed_outcome"] == "unknown"
    assert row["assessment_status"] == "not_requested"
    assert "feedback_outcome" not in row


def test_missing_correctness_does_not_become_incorrect():
    row = build_episode(_state(has_graded_outcome=True, attempt_id="a-1"), kind="graded")
    assert row is not None
    assert row["observed_outcome"] == "unknown"
    assert row["assessment_status"] == "failed"
    assert "feedback_outcome" not in row


def test_failed_attempt_write_is_not_authoritative(monkeypatch):
    row = build_episode(
        _state(has_graded_outcome=True, is_correct=False, attempt_record_failed=True),
        kind="graded",
    )
    assert row is not None
    assert row["observed_outcome"] == "unknown"
    assert "feedback_status" not in row


def test_content_mismatch_does_not_close_another_question():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(episode_id="question-a", content_id="item-a", session_id="sess-a", assistant_turn_index=2),
        _row(episode_id="question-b", content_id="item-b", session_id="sess-b", assistant_turn_index=4),
    )
    assert _close(_client(fake), content_id="item-missing", is_correct=False) is None
    assert _stored(fake, "question-a")["feedback_status"] == "pending"
    assert _stored(fake, "question-b")["feedback_status"] == "pending"


def test_replay_keeps_closed_retrieval_text():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(
            retrieval_text="feedback=incorrect",
            feedback_status="next_attempt",
            feedback_outcome="incorrect",
            embedding=[1.0],
            embed_model="kept",
        ),
    )
    client = _client(fake)
    client._episodes_enabled = True
    client._approach_enabled = True
    client._evidence_enabled = True
    saved = client.upsert_episode(
        _row(
            retrieval_text="feedback=pending",
            embedding=[9.0],
            embed_model="stale",
            feedback_status="pending",
        )
    )
    assert saved["retrieval_text"] == "feedback=incorrect"
    assert saved["embedding"] == [1.0]
    assert saved["feedback_outcome"] == "incorrect"


def test_one_attempt_counts_once_and_different_mistakes_do_not_merge():
    shared = {
        "user_id": "user-a",
        "concept_id": "torque",
        "attempt_id": "attempt-1",
        "feedback_outcome": "incorrect",
        "student_response": "torque sign flips when the force is into the page",
        "evidence_source": "dialogue",
    }
    rows = [
        {**shared, "episode_id": "teach"},
        {**shared, "episode_id": "grade"},
        {
            "episode_id": "other",
            "user_id": "user-a",
            "concept_id": "torque",
            "attempt_id": "attempt-2",
            "feedback_outcome": "incorrect",
            "student_response": "I forgot mass and used weight",
            "evidence_source": "dialogue",
        },
    ]
    assert eligible_clusters(rows, "user-a") == []


def test_same_mistake_can_be_consolidated():
    text = "torque sign flips when the force is into the page"
    rows = [
        {
            "episode_id": "e1",
            "user_id": "user-a",
            "concept_id": "torque",
            "attempt_id": "a1",
            "feedback_outcome": "incorrect",
            "student_response": text,
            "evidence_source": "dialogue",
        },
        {
            "episode_id": "e2",
            "user_id": "user-a",
            "concept_id": "torque",
            "attempt_id": "a2",
            "feedback_outcome": "incorrect",
            "student_response": text,
            "evidence_source": "dialogue",
        },
    ]
    clusters = eligible_clusters(rows, "user-a")
    assert len(clusters) == 1
    assert clusters[0]["kind"] == "misconception"


def test_recall_drops_a_stale_failure_after_improvement(monkeypatch):
    fake = _patch_db(monkeypatch)
    _patch_embed(monkeypatch)
    fake.candidates = [
        _episode(episode_id="old-miss", observed_outcome="incorrect", feedback_outcome="incorrect", concept_id="torque", created_at="2026-01-01T00:00:00+00:00"),
        _episode(episode_id="later-ok", observed_outcome="correct", feedback_outcome="correct", concept_id="torque", created_at="2026-06-01T00:00:00+00:00"),
    ]
    ids = [row["episode_id"] for row in retrieve_relevant_episodes("user-a", "torque signs", "torque")]
    assert "old-miss" not in ids
    assert "later-ok" in ids


def test_numpy_like_embedding_is_stored():
    class Vec:
        size = 2

        def __iter__(self):
            return iter([0.2, 0.4])

        def __bool__(self):
            raise ValueError("ambiguous")

    fake = FakeSupabase()
    client = SupabaseClient.__new__(SupabaseClient)
    client.client = fake
    client._memory_enabled = True

    import retrieval.embedder as embedder

    original = embedder.encode_one
    embedder.encode_one = lambda _text: Vec()
    try:
        stored = client.insert_learner_memory("user-a", "context", "Lives with a sibling who studies physics.")
    finally:
        embedder.encode_one = original
    assert stored["embedding"] == [0.2, 0.4]
