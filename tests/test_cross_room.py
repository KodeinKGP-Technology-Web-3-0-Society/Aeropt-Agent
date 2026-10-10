from services import cross_room


class _Q:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self._data})()


class _FakeDB:
    client = type("C", (), {"table": lambda self, _n: _Q([{"agent_session_id": "s-study"}])})()

    def list_user_study_sessions(self, user_id, limit=3):
        return [{"concept_name": "SI units", "current_phase": "learn", "questions_served": 0}]

    def list_user_sessions(self, user_id, limit=5):
        return [{"session_id": "s-now"}, {"session_id": "s-study"}, {"session_id": "s-chat"}]

    def read_recent_chat_turns(self, sid, n=4):
        return {
            "s-now": [{"role": "user", "content": "current turn"}],
            "s-study": [{"role": "user", "content": "what is a dimension?"}],
            "s-chat": [{"role": "user", "content": "doubt about torque"}],
        }[sid]


def test_digest_labels_rooms_and_skips_current_session(monkeypatch):
    monkeypatch.setattr(cross_room, "db", _FakeDB())
    text = cross_room.recent_activity("u1", "s-now")
    assert "SI units" in text
    assert "[Study Room] Student: what is a dimension?" in text
    assert "[Chat] Student: doubt about torque" in text
    assert "current turn" not in text


def test_no_user_no_digest():
    assert cross_room.recent_activity("") == ""
