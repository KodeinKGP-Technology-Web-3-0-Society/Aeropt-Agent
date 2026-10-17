"""Between-session consolidation. No network and no live model."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import agents.memory_consolidate as memory_consolidate
from agents.memory_consolidate import (
    consolidate_for_user,
    eligible_clusters,
    run_pending,
    validate_proposal,
)


class Store:
    def __init__(self):
        self.ready = True
        self.episodes: List[Dict[str, Any]] = []
        self.facts: List[Dict[str, Any]] = []
        self.runs: List[Dict[str, Any]] = []
        self.inserts: List[Dict[str, Any]] = []

    def consolidation_schema_ready(self) -> bool:
        return self.ready

    def list_unconsolidated_episodes(self, user_id: str) -> List[Dict[str, Any]]:
        return [
            dict(row)
            for row in self.episodes
            if row["user_id"] == user_id and not row.get("consolidated_at")
        ]

    def list_users_with_open_episodes(self) -> List[str]:
        found: List[str] = []
        for row in self.episodes:
            if not row.get("consolidated_at") and row["user_id"] not in found:
                found.append(row["user_id"])
        return found

    def active_learner_facts(self, user_id: str) -> List[Dict[str, Any]]:
        return [row for row in self.facts if row["user_id"] == user_id]

    def consolidation_seen(self, user_id: str, cluster_key: str) -> bool:
        return any(row["user_id"] == user_id and row["cluster_key"] == cluster_key for row in self.runs)

    def record_consolidation(self, **kwargs: Any) -> bool:
        self.runs.append(kwargs)
        return True

    def mark_episodes_consolidated(self, user_id: str, episode_ids: List[str], memory_id: Optional[str]) -> None:
        for row in self.episodes:
            if row["user_id"] == user_id and row["episode_id"] in episode_ids:
                row["consolidated_at"] = "now"
                row["consolidation_memory_id"] = memory_id

    def insert_learner_memory(self, user_id: str, kind: str, fact: str, **kwargs: Any) -> Dict[str, Any]:
        row = {
            "memory_id": f"mem-{len(self.facts) + 1}",
            "user_id": user_id,
            "kind": kind,
            "fact": fact,
            "subject_key": kwargs.get("subject_key"),
        }
        self.facts.append(row)
        self.inserts.append(row)
        return row


def _ep(
    episode_id: str,
    user_id: str = "user-a",
    concept: str = "torque",
    outcome: str = "incorrect",
    query: str = "sign of torque",
    approach: str = "right-hand rule",
    *,
    observed: str = "taught",
) -> Dict[str, Any]:
    return {
        "episode_id": episode_id,
        "user_id": user_id,
        "concept_id": concept,
        "feedback_outcome": outcome,
        "observed_outcome": observed,
        "query_excerpt": query,
        "approach": approach,
        "student_response": "the force and the acceleration point opposite ways",
        "evidence_source": "dialogue",
    }


def _bind(monkeypatch, store: Optional[Store] = None) -> Store:
    store = store or Store()
    monkeypatch.setattr(memory_consolidate, "db", store)
    return store


def _save(fact: str = "Flips the sign of torque.", kind: str = "misconception") -> str:
    return json.dumps(
        {
            "save": True,
            "kind": kind,
            "fact": fact,
            "concept_id": "torque",
            "confidence": 0.8,
            "user_id": "user-b",
        }
    )


def test_clusters_need_a_repeated_outcome_for_one_user():
    lone = [_ep("1")]
    assert eligible_clusters(lone, "user-a") == []
    taught = [_ep("1", outcome=None, observed="taught"), _ep("2", outcome=None, observed="taught")]
    assert eligible_clusters(taught, "user-a") == []
    mixed = [
        _ep("1", outcome="incorrect"),
        _ep("2", user_id="user-b", query="secret from b"),
        _ep("3", concept="friction", outcome="incorrect"),
    ]
    assert eligible_clusters(mixed, "user-a") == []
    later = [
        _ep("old", outcome="incorrect", approach="right-hand rule"),
        _ep("grade", outcome=None, observed="incorrect", approach="Well done, review the sign."),
    ]
    clusters = eligible_clusters(later, "user-a")
    assert len(clusters) == 1
    assert clusters[0]["kind"] == "misconception"
    assert clusters[0]["user_id"] == "user-a"
    assert {row["episode_id"] for row in clusters[0]["episodes"]} == {"old", "grade"}


def test_saves_one_fact_for_the_requested_user_and_does_not_repeat(monkeypatch):
    store = _bind(monkeypatch)
    store.episodes = [
        _ep("1", query="sign of torque"),
        _ep("2", query="torque direction"),
        _ep("b1", user_id="user-b", query="SECRET-B"),
        _ep("b2", user_id="user-b", query="SECRET-B-2"),
    ]
    prompts: List[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return _save()

    first = consolidate_for_user("user-a", complete=complete)
    assert first["saved"] is True
    assert first["reason"] == "saved"
    assert store.inserts[0]["user_id"] == "user-a"
    assert store.inserts[0]["kind"] == "misconception"
    assert store.inserts[0]["fact"] == "Flips the sign of torque."
    assert "SECRET-B" not in prompts[0]
    assert all(row["user_id"] == "user-b" or row.get("consolidated_at") for row in store.episodes)
    assert store.episodes[2].get("consolidated_at") is None

    second = consolidate_for_user("user-a", complete=complete)
    assert second["saved"] is False
    assert second["reason"] == "no_repeated_outcome"
    assert len(prompts) == 1
    assert len(store.inserts) == 1


def test_duplicate_fact_marks_episodes_without_a_second_insert(monkeypatch):
    store = _bind(monkeypatch)
    store.episodes = [_ep("1"), _ep("2")]
    store.facts.append(
        {
            "memory_id": "mem-existing",
            "user_id": "user-a",
            "kind": "misconception",
            "fact": "Flips the sign of torque.",
            "subject_key": "torque",
        }
    )
    result = consolidate_for_user("user-a", complete=lambda _prompt: _save())
    assert result["reason"] == "duplicate"
    assert result["saved"] is False
    assert store.inserts == []
    assert store.episodes[0]["consolidation_memory_id"] == "mem-existing"
    assert store.episodes[1]["consolidated_at"] == "now"


def test_rejects_a_long_fact_and_does_not_ask_the_model_again(monkeypatch):
    store = _bind(monkeypatch)
    store.episodes = [_ep("1"), _ep("2")]
    calls = {"n": 0}

    def complete(_prompt: str) -> str:
        calls["n"] += 1
        return _save(fact="x" * 301)

    first = consolidate_for_user("user-a", complete=complete)
    assert first["reason"] == "rejected"
    assert store.inserts == []
    assert store.episodes[0].get("consolidated_at") is None
    second = consolidate_for_user("user-a", complete=complete)
    assert second["reason"] == "already_considered"
    assert calls["n"] == 1


def test_does_not_store_a_preference_or_a_single_episode(monkeypatch):
    store = _bind(monkeypatch)
    store.episodes = [_ep("1")]
    assert consolidate_for_user("user-a", complete=lambda _p: _save(kind="preference"))["reason"] == "no_repeated_outcome"

    store.episodes.append(_ep("2"))
    result = consolidate_for_user("user-a", complete=lambda _p: _save(kind="preference"))
    assert result["reason"] == "rejected"
    assert store.inserts == []
    cluster = eligible_clusters(store.episodes, "user-a")[0]
    assert validate_proposal({"save": True, "kind": "hobby", "fact": "Nope.", "concept_id": "torque"}, cluster) is None
    assert validate_proposal({"save": False}, cluster) is None


def test_dry_run_writes_nothing(monkeypatch):
    store = _bind(monkeypatch)
    store.episodes = [_ep("1"), _ep("2")]
    result = consolidate_for_user("user-a", dry_run=True, complete=lambda _p: _save())
    assert result["reason"] == "would_save"
    assert result["dry_run"] is True
    assert store.inserts == []
    assert store.runs == []
    assert store.episodes[0].get("consolidated_at") is None


def test_schema_gap_and_db_errors_fail_open(monkeypatch):
    store = _bind(monkeypatch)
    store.ready = False
    store.episodes = [_ep("1"), _ep("2")]
    assert consolidate_for_user("user-a", complete=lambda _p: _save())["reason"] == "schema_not_ready"

    def boom() -> bool:
        raise RuntimeError("database url leaked")

    store.consolidation_schema_ready = boom  # type: ignore[method-assign]
    failed = consolidate_for_user("user-a", complete=lambda _p: _save())
    assert failed["reason"] == "failed_open"
    assert "leaked" not in json.dumps(failed)


def test_pending_run_keeps_each_users_episodes_apart(monkeypatch):
    store = _bind(monkeypatch)
    store.episodes = [
        _ep("a1", query="torque-a"),
        _ep("a2", query="torque-a-2"),
        _ep("b1", user_id="user-b", concept="optics", query="optics-b"),
        _ep("b2", user_id="user-b", concept="optics", query="optics-b-2"),
    ]
    prompts: List[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        concept = "torque" if "torque-a" in prompt else "optics"
        return json.dumps(
            {
                "save": True,
                "kind": "misconception",
                "fact": f"Repeats a mistake in {concept}.",
                "concept_id": concept,
                "confidence": 0.7,
            }
        )

    results = run_pending(complete=complete)
    assert [row["user_id"] for row in results] == ["user-a", "user-b"]
    assert all(row["saved"] for row in results)
    assert "optics-b" not in prompts[0]
    assert "torque-a" not in prompts[1]
    assert {row["user_id"] for row in store.inserts} == {"user-a", "user-b"}
