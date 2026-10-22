"""Retrieval cold starts happen before timed CLI turns and learner-side writes."""

from unittest.mock import Mock

import pytest

import main as cli
from retrieval import embedder, service, store


def test_cli_prepares_retrieval_before_creating_a_session_or_accepting_input(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(cli.sys, "argv", ["main.py", "--user-id", "user-1"])
    monkeypatch.setattr(store, "verify_embedding_contract", lambda: events.append("contract"))
    monkeypatch.setattr(service, "warm", lambda: events.append("warm") or {"warmed": True})

    def create_session(**kwargs):
        events.append("session")
        return "session-1"

    def read_input(prompt):
        events.append("input")
        assert "Course retrieval ready" in capsys.readouterr().out
        return "quit"

    monkeypatch.setattr(cli, "resolve_session", create_session)
    monkeypatch.setattr("builtins.input", read_input)
    monkeypatch.setattr(cli, "_flush_session_summary", lambda *args: events.append("flush"))
    cli.main()
    assert events == ["contract", "warm", "session", "input", "flush"]


@pytest.mark.parametrize("problem", [embedder.EmbeddingMismatch("wrong vector width"), RuntimeError("database unavailable")])
def test_cli_stops_before_registration_when_retrieval_contract_fails(monkeypatch, capsys, problem):
    monkeypatch.setattr(cli.sys, "argv", ["main.py", "--register"])

    def invalid_contract():
        raise problem

    monkeypatch.setattr(store, "verify_embedding_contract", invalid_contract)
    warm, register, session = Mock(), Mock(), Mock()
    monkeypatch.setattr(service, "warm", warm)
    monkeypatch.setattr(cli, "_register_interactive", register)
    monkeypatch.setattr(cli, "resolve_session", session)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    warm.assert_not_called()
    register.assert_not_called()
    session.assert_not_called()
    assert "RETRIEVAL_DATABASE_URL" in capsys.readouterr().out


def test_cli_does_not_announce_readiness_when_warming_fails(monkeypatch, capsys):
    monkeypatch.setattr(store, "verify_embedding_contract", lambda: None)
    monkeypatch.setattr(service, "warm", lambda: {"warmed": False, "reason": "empty index"})
    assert cli._prepare_retrieval() is False
    output = capsys.readouterr().out
    assert "[ERROR]" in output
    assert "Course retrieval ready" not in output


def test_cli_does_not_announce_readiness_when_model_loading_fails(monkeypatch, capsys):
    monkeypatch.setattr(store, "verify_embedding_contract", lambda: None)
    monkeypatch.setattr(service, "warm", Mock(side_effect=OSError("model files unavailable")))
    assert cli._prepare_retrieval() is False
    assert "Course retrieval ready" not in capsys.readouterr().out
