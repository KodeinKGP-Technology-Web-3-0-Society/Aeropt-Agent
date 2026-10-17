"""Phase 0 contract for episodic_memory client methods. No network."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from db.supabase_client import SupabaseClient


Filter = Tuple[str, str, Any]


class FakeQuery:
    def __init__(self, db: "FakeSupabase", table: str):
        self.db = db
        self.table = table
        self.filters: List[Filter] = []
        self.op = "select"
        self.payload: Any = None
        self.columns = "*"
        self.limit_n: Optional[int] = None
        self.order_col: Optional[str] = None
        self.order_desc = False
        self.on_conflict = ""
        self.default_to_null = True
        self.keys: List[str] = []

    def select(self, columns: str = "*"):
        self.op = "select"
        self.columns = columns
        return self

    def upsert(self, payload, on_conflict: str = "", default_to_null: bool = True):
        self.op = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        self.default_to_null = default_to_null
        self.keys = [key.strip() for key in on_conflict.split(",") if key.strip()]
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def order(self, col, desc: bool = False):
        self.order_col = col
        self.order_desc = desc
        return self

    def limit(self, n: int):
        self.limit_n = n
        return self

    def _matches(self, row: Dict[str, Any]) -> bool:
        for kind, col, val in self.filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "neq" and row.get(col) == val:
                return False
            if kind == "is" and val == "null" and row.get(col) is not None:
                return False
        return True

    def _project(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if not self.columns or self.columns.strip() == "*":
            return dict(row)
        cols = [col.strip() for col in self.columns.split(",") if col.strip()]
        return {col: row.get(col) for col in cols}

    def execute(self):
        self.db.attempts += 1
        if self.db.missing:
            raise RuntimeError(
                "Could not find the table 'public.episodic_memory' in the schema cache"
            )
        if self.db.error is not None:
            raise self.db.error
        self.db.queries.append(
            {
                "op": self.op,
                "filters": list(self.filters),
                "limit": self.limit_n,
                "columns": self.columns,
            }
        )
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            stored = self._apply_upsert(rows)
            return type("R", (), {"data": [dict(stored)]})()
        if self.op == "update":
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    updated.append(dict(row))
            return type("R", (), {"data": updated})()
        if self.op == "insert":
            item = dict(self.payload)
            rows.append(item)
            return type("R", (), {"data": [item]})()
        hit = [row for row in rows if self._matches(row)]
        if self.order_col:
            hit.sort(key=lambda row: row.get(self.order_col) or "", reverse=self.order_desc)
        if self.limit_n is not None:
            hit = hit[: self.limit_n]
        return type("R", (), {"data": [self._project(row) for row in hit]})()

    def _apply_upsert(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        item = dict(self.payload)
        self.db.upserts.append(
            {
                "payload": dict(item),
                "on_conflict": self.on_conflict,
                "default_to_null": self.default_to_null,
            }
        )
        match = None
        for row in rows:
            if self.keys and all(row.get(key) == item.get(key) for key in self.keys):
                match = row
                break
        if match is None:
            if "episode_id" not in item:
                item["episode_id"] = str(uuid.uuid4())
            rows.append(item)
            return item
        if self.default_to_null:
            for col in list(match.keys()):
                if col not in item and col not in self.keys and col != "episode_id":
                    match[col] = None
        match.update(item)
        return match


class FakeSupabase:
    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.missing = False
        self.error: Optional[BaseException] = None
        self.attempts = 0
        self.queries: List[Dict[str, Any]] = []
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name: str):
        return FakeQuery(self, name)


def _client(fake: FakeSupabase) -> SupabaseClient:
    client = SupabaseClient.__new__(SupabaseClient)
    client.client = fake
    client._memory_enabled = None
    client._episodes_enabled = None
    client._agent_used_is_narrow = False
    client.redis = None
    return client


def _row(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "episode_id": "ep-1",
        "user_id": "user-a",
        "session_id": "sess-1",
        "assistant_turn_index": 2,
        "surface": "chat",
        "route": "agent_loop",
        "task_type": "doubt",
        "retrieval_text": "surface=chat concept=torque",
        "status": "active",
        "feedback_status": "pending",
        "concept_id": "torque",
        "created_at": "2026-01-02T00:00:00+00:00",
        "observed_outcome": "taught",
        "query_excerpt": "sign of torque",
        "actions": ["tool:search"],
    }
    base.update(overrides)
    return base


def _seed(fake: FakeSupabase, *rows: Dict[str, Any]) -> None:
    fake.tables.setdefault("episodic_memory", []).extend(rows)


def _stored(fake: FakeSupabase, episode_id: str) -> Dict[str, Any]:
    for row in fake.tables["episodic_memory"]:
        if row["episode_id"] == episode_id:
            return row
    raise AssertionError(episode_id)


def _close(client: SupabaseClient, **overrides: Any) -> Optional[str]:
    args: Dict[str, Any] = {
        "user_id": "user-a",
        "concept_id": "torque",
        "exclude_turn_index": 99,
        "content_id": None,
        "is_correct": False,
        "attempt_id": "attempt-1",
        "proficiency_before": None,
        "proficiency_after": None,
    }
    args.update(overrides)
    return client.close_pending_episode(**args)


def _eq_filters(query: Dict[str, Any]) -> Dict[str, Any]:
    return {col: val for kind, col, val in query["filters"] if kind == "eq"}


def test_init_keeps_episode_cache_independent_of_memory_cache(monkeypatch):
    monkeypatch.setattr("db.supabase_client.create_client", lambda url, key: object())
    monkeypatch.setattr("db.supabase_client._make_redis_client", lambda: None)
    client = SupabaseClient("http://localhost", "test-key")
    assert client._memory_enabled is None
    assert client._episodes_enabled is None


def test_episodes_enabled_caches_a_present_table():
    fake = FakeSupabase()
    client = _client(fake)
    assert client.episodes_enabled() is True
    assert client.episodes_enabled() is True
    assert fake.attempts == 1
    assert client._memory_enabled is None


def test_episodes_enabled_caches_missing_table_only():
    fake = FakeSupabase()
    fake.missing = True
    client = _client(fake)
    assert client.episodes_enabled() is False
    assert client._episodes_enabled is False
    assert client.episodes_enabled() is False
    assert fake.attempts == 1


def test_transient_failure_does_not_cache_false():
    fake = FakeSupabase()
    fake.error = RuntimeError("connection reset")
    client = _client(fake)
    assert client.episodes_enabled() is False
    assert client._episodes_enabled is None
    fake.error = None
    assert client.episodes_enabled() is True
    assert client._episodes_enabled is True


def test_enabling_episodes_does_not_change_memory_flag():
    client = _client(FakeSupabase())
    client._memory_enabled = False
    assert client.episodes_enabled() is True
    assert client._memory_enabled is False
    assert client._episodes_enabled is True


def test_upsert_uses_the_turn_key_and_does_not_duplicate():
    fake = FakeSupabase()
    client = _client(fake)
    first = client.upsert_episode(_row(retrieval_text="first"))
    second = client.upsert_episode(_row(retrieval_text="second", observed_outcome="taught"))
    assert first is not None and second is not None
    assert fake.upserts[0]["on_conflict"] == "session_id,assistant_turn_index"
    assert fake.upserts[0]["default_to_null"] is False
    assert len(fake.tables["episodic_memory"]) == 1
    assert second["retrieval_text"] == "second"
    assert second["user_id"] == "user-a"


def test_upsert_refuses_a_different_user_on_the_same_turn():
    fake = FakeSupabase()
    client = _client(fake)
    assert client.upsert_episode(_row(retrieval_text="original")) is not None
    refused = client.upsert_episode(_row(user_id="user-b", retrieval_text="hijack"))
    assert refused is None
    assert len(fake.upserts) == 1
    assert _stored(fake, "ep-1")["user_id"] == "user-a"
    assert _stored(fake, "ep-1")["retrieval_text"] == "original"


def test_upsert_strips_null_feedback_fields():
    fake = FakeSupabase()
    client = _client(fake)
    row = _row(feedback_status=None, feedback_outcome=None, feedback_attempt_id=None)
    stored = client.upsert_episode(row)
    assert stored is not None
    payload = fake.upserts[-1]["payload"]
    assert "feedback_status" not in payload
    assert "feedback_outcome" not in payload
    assert "feedback_attempt_id" not in payload
    assert row["feedback_status"] is None


def test_non_grade_replay_does_not_reopen_closed_feedback():
    fake = FakeSupabase()
    client = _client(fake)
    assert client.upsert_episode(
        _row(
            feedback_status="next_attempt",
            feedback_outcome="incorrect",
            feedback_attempt_id="attempt-9",
            retrieval_text="old",
        )
    ) is not None
    stored = client.upsert_episode(
        _row(feedback_status="pending", feedback_outcome=None, retrieval_text="new")
    )
    assert stored is not None
    assert stored["retrieval_text"] == "old"
    assert stored["feedback_status"] == "next_attempt"
    assert stored["feedback_outcome"] == "incorrect"
    assert stored["feedback_attempt_id"] == "attempt-9"
    assert fake.upserts[-1]["payload"].get("feedback_status") != "pending"
    assert "feedback_outcome" not in fake.upserts[-1]["payload"]


def test_upsert_returns_none_when_required_fields_are_missing_or_the_table_is_down():
    fake = FakeSupabase()
    client = _client(fake)
    assert client.upsert_episode({"user_id": "user-a"}) is None
    assert client.upsert_episode(_row(retrieval_text="")) is None
    assert client.upsert_episode(_row(assistant_turn_index=None)) is None
    assert fake.upserts == []

    missing = FakeSupabase()
    missing.missing = True
    assert _client(missing).upsert_episode(_row()) is None


def test_close_pending_updates_only_the_newest_older_same_concept_row():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(episode_id="older", assistant_turn_index=2, created_at="2026-01-01T00:00:00+00:00"),
        _row(
            episode_id="newer",
            session_id="sess-2",
            assistant_turn_index=4,
            created_at="2026-01-03T00:00:00+00:00",
        ),
        _row(
            episode_id="other-concept",
            concept_id="friction",
            session_id="sess-3",
            assistant_turn_index=2,
            created_at="2026-01-05T00:00:00+00:00",
        ),
        _row(
            episode_id="other-user",
            user_id="user-b",
            session_id="sess-4",
            assistant_turn_index=2,
            created_at="2026-01-06T00:00:00+00:00",
        ),
    )
    client = _client(fake)
    assert _close(client, exclude_turn_index=8, is_correct=False) == "newer"
    assert _stored(fake, "newer")["feedback_status"] == "next_attempt"
    assert _stored(fake, "newer")["feedback_outcome"] == "incorrect"
    assert _stored(fake, "newer")["feedback_attempt_id"] == "attempt-1"
    assert _stored(fake, "older")["feedback_status"] == "pending"
    assert _stored(fake, "other-concept")["feedback_status"] == "pending"
    assert _stored(fake, "other-user")["feedback_status"] == "pending"

    updates = [query for query in fake.queries if query["op"] == "update"]
    assert updates
    filters = _eq_filters(updates[-1])
    assert filters["episode_id"] == "newer"
    assert filters["user_id"] == "user-a"
    assert filters["feedback_status"] == "pending"

    # The same attempt must not close the next pending row.
    assert _close(client, exclude_turn_index=8, is_correct=True) == "newer"
    assert _stored(fake, "older")["feedback_status"] == "pending"
    assert _stored(fake, "newer")["feedback_outcome"] == "incorrect"

    assert _close(client, exclude_turn_index=8, is_correct=True, attempt_id="attempt-2") == "older"
    assert _stored(fake, "older")["feedback_outcome"] == "correct"
    assert _stored(fake, "newer")["feedback_outcome"] == "incorrect"
    assert _close(client, exclude_turn_index=8, attempt_id="attempt-2") == "older"


def test_same_attempt_closes_at_most_one_pending_episode():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(episode_id="first", created_at="2026-01-03T00:00:00+00:00"),
        _row(
            episode_id="second",
            session_id="sess-2",
            assistant_turn_index=4,
            created_at="2026-01-01T00:00:00+00:00",
        ),
    )
    client = _client(fake)
    assert _close(client, attempt_id="attempt-a", is_correct=False) == "first"
    assert _stored(fake, "first")["feedback_status"] == "next_attempt"
    assert _stored(fake, "first")["feedback_attempt_id"] == "attempt-a"
    assert _stored(fake, "second")["feedback_status"] == "pending"

    assert _close(client, attempt_id="attempt-a", is_correct=True) == "first"
    assert _stored(fake, "second")["feedback_status"] == "pending"
    assert _stored(fake, "first")["feedback_outcome"] == "incorrect"

    assert _close(client, attempt_id="attempt-b", is_correct=True) == "second"
    assert _stored(fake, "second")["feedback_status"] == "next_attempt"
    assert _stored(fake, "second")["feedback_attempt_id"] == "attempt-b"
    assert _stored(fake, "first")["feedback_status"] == "next_attempt"


def test_retrieval_update_does_not_touch_feedback_columns():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(
            feedback_status="next_attempt",
            feedback_outcome="incorrect",
            feedback_attempt_id="attempt-a",
            embedding=[0.1, 0.2],
            embed_model="old-model",
            retrieval_text="feedback=pending",
        ),
    )
    client = _client(fake)
    client.update_episode_retrieval(
        user_id="user-a",
        episode_id="ep-1",
        retrieval_text="feedback=incorrect",
        embedding=[0.9, 0.1],
        embed_model="new-model",
    )
    stored = _stored(fake, "ep-1")
    assert stored["retrieval_text"] == "feedback=incorrect"
    assert stored["embedding"] == [0.9, 0.1]
    assert stored["embed_model"] == "new-model"
    assert stored["feedback_status"] == "next_attempt"
    assert stored["feedback_outcome"] == "incorrect"
    assert stored["feedback_attempt_id"] == "attempt-a"


def test_close_pending_skips_the_current_turn_and_prefers_content():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(
            episode_id="content-match",
            assistant_turn_index=2,
            content_id="item-x",
            created_at="2026-01-01T00:00:00+00:00",
            proficiency_before=0.2,
            proficiency_after=None,
        ),
        _row(
            episode_id="newer-other",
            session_id="sess-2",
            assistant_turn_index=4,
            content_id="item-y",
            created_at="2026-01-03T00:00:00+00:00",
        ),
        _row(
            episode_id="current",
            session_id="sess-3",
            assistant_turn_index=6,
            content_id="item-x",
            created_at="2026-01-04T00:00:00+00:00",
        ),
    )
    client = _client(fake)
    assert _close(
        client,
        exclude_turn_index=6,
        content_id="item-x",
        is_correct=True,
        proficiency_before=0.9,
        proficiency_after=0.4,
    ) == "content-match"
    stored = _stored(fake, "content-match")
    assert stored["feedback_outcome"] == "correct"
    assert stored["proficiency_before"] == 0.2
    assert stored["proficiency_after"] == 0.4
    assert _stored(fake, "newer-other")["feedback_status"] == "pending"
    assert _stored(fake, "current")["feedback_status"] == "pending"

    only = FakeSupabase()
    _seed(only, _row(episode_id="current", assistant_turn_index=4))
    assert _close(_client(only), exclude_turn_index=4) is None
    assert _stored(only, "current")["feedback_status"] == "pending"


def test_close_pending_falls_back_to_newest_when_content_does_not_match():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(episode_id="older", content_id="item-x", created_at="2026-01-01T00:00:00+00:00"),
        _row(
            episode_id="newer",
            session_id="sess-2",
            assistant_turn_index=4,
            content_id="item-y",
            created_at="2026-01-03T00:00:00+00:00",
        ),
    )
    assert _close(_client(fake), content_id="item-z") is None
    assert _close(_client(fake)) == "newer"


def test_fetch_known_concept_merges_24_and_16_and_dedupes():
    fake = FakeSupabase()
    concept_rows = [
        _row(
            episode_id=f"a{i:02d}",
            session_id=f"sa{i}",
            assistant_turn_index=2,
            created_at=f"2026-01-{i:02d}T00:00:00+00:00",
        )
        for i in range(1, 25)
    ]
    other_rows = [
        _row(
            episode_id=f"b{i:02d}",
            session_id=f"sb{i}",
            assistant_turn_index=2,
            concept_id="friction",
            created_at=f"2026-02-{i:02d}T00:00:00+00:00",
        )
        for i in range(1, 11)
    ]
    _seed(
        fake,
        *concept_rows,
        *other_rows,
        _row(episode_id="invalid", session_id="s-inv", status="invalid", created_at="2026-03-01T00:00:00+00:00"),
        _row(
            episode_id="user-b",
            user_id="user-b",
            session_id="s-b",
            created_at="2026-04-01T00:00:00+00:00",
        ),
    )
    active = [
        row
        for row in fake.tables["episodic_memory"]
        if row["user_id"] == "user-a" and row["status"] == "active"
    ]
    newest = sorted(active, key=lambda row: row["created_at"], reverse=True)
    concept = [row for row in newest if row["concept_id"] == "torque"][:24]
    expected: List[str] = []
    for row in concept + newest[:16]:
        if row["episode_id"] not in expected:
            expected.append(row["episode_id"])

    got = _client(fake).fetch_episode_candidates("user-a", "torque")
    ids = [row["episode_id"] for row in got]
    assert ids == expected
    assert len(ids) == len(set(ids))
    assert len(concept) == 24
    assert len(newest[:16]) == 16
    assert all(row["user_id"] == "user-a" for row in got)
    assert "invalid" not in ids
    assert "user-b" not in ids

    limits = [query["limit"] for query in fake.queries if query["op"] == "select" and query["limit"] in (24, 16)]
    assert limits == [24, 16]


def test_fetch_without_concept_returns_newest_40_for_that_user():
    fake = FakeSupabase()
    rows = [
        _row(
            episode_id=f"e{i:02d}",
            session_id=f"s{i}",
            assistant_turn_index=2,
            created_at=f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T00:00:00+00:00",
        )
        for i in range(45)
    ]
    _seed(
        fake,
        *rows,
        _row(episode_id="invalid", session_id="s-inv", status="invalid", created_at="2026-12-01T00:00:00+00:00"),
        _row(episode_id="user-b", user_id="user-b", session_id="s-b", created_at="2026-12-02T00:00:00+00:00"),
    )
    got = _client(fake).fetch_episode_candidates("user-a", None)
    ids = [row["episode_id"] for row in got]
    assert len(ids) == 40
    assert ids[0] == "e44"
    assert "e00" not in ids and "e04" not in ids
    assert "e05" in ids
    assert "invalid" not in ids and "user-b" not in ids
    assert fake.queries[-1]["limit"] == 40
    assert _client(fake).fetch_episode_candidates("", "torque") == []


def test_fetch_returns_empty_when_the_table_or_query_fails():
    missing = FakeSupabase()
    missing.missing = True
    assert _client(missing).fetch_episode_candidates("user-a", "torque") == []

    fake = FakeSupabase()
    client = _client(fake)
    assert client.episodes_enabled() is True
    fake.error = RuntimeError("boom")
    assert client.fetch_episode_candidates("user-a", None) == []


def test_invalidate_session_and_turn_set_status_invalid():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(episode_id="a", session_id="sess-1", assistant_turn_index=2),
        _row(episode_id="b", session_id="sess-1", assistant_turn_index=4, created_at="2026-01-03T00:00:00+00:00"),
        _row(episode_id="c", session_id="sess-2", created_at="2026-01-04T00:00:00+00:00"),
    )
    client = _client(fake)
    client.invalidate_turn("sess-1", 2)
    assert _stored(fake, "a")["status"] == "invalid"
    assert _stored(fake, "b")["status"] == "active"
    assert _stored(fake, "c")["status"] == "active"

    client.invalidate_session("sess-1")
    assert _stored(fake, "a")["status"] == "invalid"
    assert _stored(fake, "b")["status"] == "invalid"
    assert _stored(fake, "c")["status"] == "active"
    got = client.fetch_episode_candidates("user-a", None)
    assert [row["episode_id"] for row in got] == ["c"]


def test_invalidate_swallows_a_missing_table():
    fake = FakeSupabase()
    fake.missing = True
    client = _client(fake)
    client.invalidate_session("sess-1")
    client.invalidate_turn("sess-1", 2)
    assert client._episodes_enabled is False


def test_upsert_keeps_approach_only_when_the_column_exists():
    fake = FakeSupabase()
    client = _client(fake)
    client._episodes_enabled = True
    client._approach_enabled = False
    assert client.upsert_episode(_row(approach="Use the right-hand rule.")) is not None
    assert "approach" not in _stored(fake, "ep-1")

    client._approach_enabled = True
    assert client.upsert_episode(_row(approach="Use the right-hand rule."))["approach"] == "Use the right-hand rule."
    assert _stored(fake, "ep-1")["approach"] == "Use the right-hand rule."


def test_consolidation_mark_and_list_stay_on_one_user():
    fake = FakeSupabase()
    _seed(
        fake,
        _row(episode_id="ep-a", consolidated_at=None),
        _row(
            episode_id="ep-b",
            user_id="user-b",
            session_id="sess-b",
            assistant_turn_index=4,
            query_excerpt="other student",
        ),
    )
    fake.tables["learner_memory"] = []
    client = _client(fake)
    client._episodes_enabled = True
    client._memory_enabled = True
    client._consolidated_at_enabled = True
    client._consolidation_table_enabled = True

    listed = client.list_unconsolidated_episodes("user-a")
    assert [row["episode_id"] for row in listed] == ["ep-a"]
    assert client.list_users_with_open_episodes() == ["user-a", "user-b"]

    client.mark_episodes_consolidated("user-a", ["ep-a", "ep-b"], "mem-1")
    assert _stored(fake, "ep-a")["consolidated_at"]
    assert _stored(fake, "ep-a")["consolidation_memory_id"] == "mem-1"
    assert _stored(fake, "ep-b").get("consolidated_at") is None
    assert client.list_unconsolidated_episodes("user-a") == []
