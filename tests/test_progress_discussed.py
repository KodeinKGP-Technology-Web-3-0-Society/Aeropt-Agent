"""A concept studied in chat but never practised still shows up as studied."""

from __future__ import annotations

from datetime import datetime, timezone

from services import progress_report


def _fake_load(events, states=()):
    def load(_user_id):
        return {"events": list(events), "states": list(states), "reviews": [], "readiness": {}, "memories": []}

    return load


def _meta(ids):
    return {cid: {"name": cid.replace("_", " "), "chapter": "Ch", "unit": "U"} for cid in ids if cid}


def _turn(cid, when="2026-10-18T10:00:00+00:00"):
    return {"kind": "tutor_turn", "source": "chat", "concept_id": cid, "created_at": when, "payload": {"query": "q"}}


def test_chat_only_concept_listed_as_discussed(monkeypatch):
    monkeypatch.setattr(progress_report, "_load", _fake_load([_turn("aldol_and_cannizzaro"), _turn(None)]))
    monkeypatch.setattr(progress_report, "_concept_meta", _meta)

    report = progress_report.build_report("u1")

    [concept] = report["concepts"]
    assert concept["concept_id"] == "aldol_and_cannizzaro"
    assert concept["discussed_only"] is True
    assert concept["status"] == "new" and concept["attempts"] == 0
    assert report["overview"]["concepts_mastered"] == 0
    assert report["chapters"] == []  # no score to average


def test_practised_concept_not_duplicated_and_chapter_unchanged(monkeypatch):
    state = {"concept_id": "torque", "proficiency_score": 0.6, "attempts": 2, "correct": 1,
             "flagged_weak": False, "last_seen_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(progress_report, "_load", _fake_load([_turn("torque"), _turn("aldol")], [state]))
    monkeypatch.setattr(progress_report, "_concept_meta", _meta)

    report = progress_report.build_report("u1")

    by_id = {c["concept_id"]: c for c in report["concepts"]}
    assert set(by_id) == {"torque", "aldol"}
    assert "discussed_only" not in by_id["torque"]
    [chapter] = report["chapters"]
    assert chapter["concepts"] == 1 and chapter["proficiency"] == 0.6


def test_digest_mentions_chat_topics_without_practice(monkeypatch):
    monkeypatch.setattr(progress_report, "_load", _fake_load([_turn("aldol_and_cannizzaro")]))
    monkeypatch.setattr(progress_report, "_concept_meta", _meta)

    digest = progress_report.progress_digest("u1")

    assert "not practised yet: aldol and cannizzaro" in digest
