"""Setup diagnostics must not echo credentials."""

from __future__ import annotations

import json
import logging

import agents.memory_status as memory_status
from agents.memory_status import collect_status, log_episodic_status


def test_status_reports_gaps_without_secrets(monkeypatch):
    monkeypatch.setenv(
        "RETRIEVAL_DATABASE_URL",
        "postgresql://postgres:super-secret-password@db.example/postgres",
    )
    monkeypatch.setattr(memory_status.db, "episodes_enabled", lambda: False)
    monkeypatch.setattr(memory_status.db, "memory_enabled", lambda: False)
    monkeypatch.setattr(memory_status.db, "_column_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(memory_status.db, "episode_approach_enabled", lambda: False)

    report = collect_status()
    blob = json.dumps(report)
    assert "super-secret-password" not in blob
    assert "postgresql://" not in blob
    assert report["ok"] is False
    assert report["checks"]["episodic_memory_table"] is False
    assert report["checks"]["retrieval_database_url_configured"] is True
    assert report["checks"]["consolidation_table"] is False


def test_status_log_swallows_probe_failures(monkeypatch, caplog):
    def boom():
        raise RuntimeError("postgresql://postgres:super-secret-password@db.example/postgres")

    monkeypatch.setattr(memory_status, "collect_status", boom)
    log = logging.getLogger("test.episodic.status")
    with caplog.at_level(logging.WARNING):
        log_episodic_status(log)
    assert "episodic memory status check failed" in caplog.text
    assert "super-secret-password" not in caplog.text
