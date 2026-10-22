"""CLI session exits persist the final summary through the shared turn service."""

import pytest

import main as cli


@pytest.mark.parametrize("termination", ["quit", "eof", "interrupt"])
def test_cli_exit_flushes_session_summary(monkeypatch, termination):
    monkeypatch.setattr(cli.sys, "argv", ["main.py", "--user-id", "user-1"])
    monkeypatch.setattr(cli, "_prepare_retrieval", lambda: True)
    monkeypatch.setattr(cli, "resolve_session", lambda **kwargs: "session-1")

    def read_input(_prompt):
        if termination == "eof":
            raise EOFError
        if termination == "interrupt":
            raise KeyboardInterrupt
        return "quit"

    monkeypatch.setattr("builtins.input", read_input)
    summaries = []
    monkeypatch.setattr(
        "services.turn_service.context_manager_node",
        lambda state: summaries.append(dict(state)) or {},
    )

    cli.main()

    assert summaries == [{"session_id": "session-1", "user_id": "user-1"}]


def test_cli_stores_the_assistant_reply(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["main.py", "--user-id", "user-1"])
    monkeypatch.setattr(cli, "_prepare_retrieval", lambda: True)
    monkeypatch.setattr(cli, "resolve_session", lambda **kwargs: "session-1")
    answers = iter(["Teach me Newton's laws", "quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    class _Graph:
        def invoke(self, _state):
            return {
                "response": "Are mg and N an action-reaction pair?",
                "agent_used": "learning_agent",
                "interaction_count": 1,
                "primary_concept_id": "newton_s_first_law_of_motion",
            }

    monkeypatch.setattr(cli, "tutor_graph", _Graph())
    monkeypatch.setattr(
        "services.turn_service.context_manager_node",
        lambda _state: {},
    )

    stored = []

    class _Db:
        def assistant_turn_contents(self, session_id, turn_index):
            return [
                row["content"]
                for row in stored
                if row["session_id"] == session_id and row["turn_index"] == turn_index
            ]

        def insert_chat_turn(self, **kwargs):
            stored.append(kwargs)
            return kwargs

    monkeypatch.setattr("services.turn_service.db", _Db())

    cli.main()

    assert stored == [
        {
            "session_id": "session-1",
            "role": "assistant",
            "content": "Are mg and N an action-reaction pair?",
            "turn_index": 2,
            "agent_used": "learning_agent",
            "concepts_referenced": ["newton_s_first_law_of_motion"],
        }
    ]


def test_assistant_reply_is_not_stored_twice(monkeypatch):
    from services.turn_service import persist_assistant_reply

    stored = []

    class _Db:
        def assistant_turn_contents(self, session_id, turn_index):
            return [
                row["content"]
                for row in stored
                if row["session_id"] == session_id and row["turn_index"] == turn_index
            ]

        def insert_chat_turn(self, **kwargs):
            stored.append(kwargs)
            return kwargs

    monkeypatch.setattr("services.turn_service.db", _Db())
    result = {
        "response": "Are mg and N an action-reaction pair?",
        "agent_used": "learning_agent",
        "interaction_count": 1,
    }
    persist_assistant_reply("session-1", result)
    persist_assistant_reply("session-1", result)
    assert len(stored) == 1
