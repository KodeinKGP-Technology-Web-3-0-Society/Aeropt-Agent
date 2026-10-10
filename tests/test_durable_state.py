"""A restarted agent (wiped disk) gets its ledger, carry and diagrams back from Supabase."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services import durable_state


class FakeQuery:
    def __init__(self, db: "FakeSupabase", table: str):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None
        self.window = None

    def select(self, *_a):
        self.op = "select"
        return self

    def upsert(self, payload, on_conflict=""):
        self.op, self.payload, self.keys = "upsert", payload, on_conflict.split(",")
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) >= val)
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def limit(self, n):
        self.window = (0, n - 1)
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            for item in self.payload if isinstance(self.payload, list) else [self.payload]:
                rows[:] = [r for r in rows if any(r.get(k) != item.get(k) for k in self.keys)]
                rows.append(dict(item))
            return type("R", (), {"data": []})()
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            rows[:] = [r for r in rows if r not in hit]
            return type("R", (), {"data": hit})()
        if self.window:
            hit = hit[self.window[0]: self.window[1] + 1]
        return type("R", (), {"data": [dict(r) for r in hit]})()


class FakeBucket:
    def __init__(self, files: Dict[str, bytes]):
        self.files = files

    def upload(self, path, data, _opts=None):
        self.files[path] = data

    def download(self, path):
        if path not in self.files:
            raise RuntimeError("Object not found")
        return self.files[path]


class FakeSupabase:
    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.files: Dict[str, bytes] = {}
        self.storage = type("S", (), {"from_": lambda _s, _b: FakeBucket(self.files)})()

    def table(self, name):
        return FakeQuery(self, name)


@pytest.fixture()
def remote(monkeypatch, tmp_path):
    fake = FakeSupabase()
    monkeypatch.setenv("AEROPT_DURABLE_STATE", "supabase")
    monkeypatch.setattr(durable_state, "_client", lambda: fake)
    durable_state.reset_for_tests()
    yield fake
    durable_state.reset_for_tests()


def _restart(module):
    durable_state.drain()
    durable_state._hydrated.clear()
    module.reset_caches()


def test_ledger_survives_a_wiped_disk(remote, monkeypatch, tmp_path):
    from retrieval import store

    monkeypatch.setattr(store, "LEDGER_DB", tmp_path / "a" / "ledger.sqlite3")
    store.reset_caches()
    item = {"content_id": "pyq_1", "concept_id": "torque", "content_type": "PYQ", "fingerprint": "fp1"}
    store.mark_served("u1", [item])
    store.mark_served("u1", [item])

    _restart(store)
    monkeypatch.setattr(store, "LEDGER_DB", tmp_path / "b" / "ledger.sqlite3")  # fresh container
    assert set(store.served_ids("u1")) == {"pyq_1"}
    assert store.served_fingerprints("u1") == {"fp1"}
    assert store.ledger().execute("SELECT times FROM served").fetchone()["times"] == 2

    store.forget_served("u1")
    _restart(store)
    monkeypatch.setattr(store, "LEDGER_DB", tmp_path / "c" / "ledger.sqlite3")
    assert store.served_ids("u1") == {}
    store.reset_caches()


def test_carry_survives_a_wiped_disk_until_released(remote, monkeypatch, tmp_path):
    import carry_store

    monkeypatch.setattr(carry_store, "_STORE_DIR", tmp_path / "a")
    carry_store.save_carry("s1", {"awaiting_graded_response": True})
    durable_state.drain()

    monkeypatch.setattr(carry_store, "_STORE_DIR", tmp_path / "b")
    assert carry_store.get_carry("s1") == {"awaiting_graded_response": True}

    carry_store.release_carry("s1")
    durable_state.drain()
    monkeypatch.setattr(carry_store, "_STORE_DIR", tmp_path / "c")
    assert carry_store.get_carry("s1") == {}


def test_diagram_is_served_from_storage_after_a_restart(remote, monkeypatch, tmp_path):
    from services import diagrams

    monkeypatch.setattr(diagrams, "SVG_DIR", tmp_path / "svg")
    remote.files["svg/0123456789abcdef0123.svg"] = b"<svg/>"
    path = diagrams.diagram_path("0123456789abcdef0123")
    assert path is not None and path.read_bytes() == b"<svg/>"
    assert diagrams.diagram_path("ffffffffffffffffffff") is None


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("AEROPT_DURABLE_STATE", raising=False)
    monkeypatch.setattr(durable_state, "_client", lambda: (_ for _ in ()).throw(AssertionError("no calls")))
    durable_state.save_carry("s", {})
    assert durable_state.load_carry("s", 60) is None
    assert durable_state.load_diagram("x") is None
    durable_state.drain()
