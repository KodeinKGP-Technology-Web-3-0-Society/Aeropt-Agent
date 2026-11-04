"""Async build, status, history and start/answer/submit rules, against an in-memory Supabase fake."""

import copy
import json
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mock_test_agent import assembly
from mock_test_agent.router import router

USER = "user-a"


class _Resp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters = "select", None, []
        self._order = None
        self._range = None
        self._count = None
        self._conflict = None

    def select(self, *_a, count=None, **_k):
        self.op, self._count = "select", count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self._conflict = "upsert", payload, on_conflict
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def order(self, k, desc=False):
        self._order = (k, desc)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def limit(self, _n):
        return self

    def _match(self, row):
        return all(row.get(k) == v for k, v in self.filters)

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            for it in items:
                it = copy.deepcopy(it)
                it.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                rows.append(it)
            return _Resp(items)
        if self.op == "upsert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = self._conflict.split(",")
            for it in items:
                for r in rows:
                    if all(r.get(k) == it.get(k) for k in keys):
                        r.update(copy.deepcopy(it))
                        break
                else:
                    rows.append(copy.deepcopy(it))
            return _Resp(items)
        if self.op == "update":
            for r in rows:
                if self._match(r):
                    r.update(copy.deepcopy(self.payload))
            return _Resp([])
        out = [copy.deepcopy(r) for r in rows if self._match(r)]
        if self._order:
            out.sort(key=lambda r: r.get(self._order[0]) or "", reverse=self._order[1])
        total = len(out)
        if self._range:
            out = out[self._range[0]:self._range[1] + 1]
        return _Resp(out, total if self._count else None)


class FakeDB:
    def __init__(self):
        self.tables = {}
        self.client = self

    def table(self, name):
        return _Query(self, name)


def _q(cid, qtype="single_correct", answer=None):
    return {
        "content_id": cid, "concept_id": None, "question_type": qtype, "difficulty": 3,
        "core_text": "stem", "fingerprint": f"fp_{cid}",
        "structured": json.dumps({"stem": f"stem {cid}", "options": [
            {"label": "A", "text": "x"}, {"label": "B", "text": "y"}]} if qtype != "numeric" else {"stem": "n"}),
        "answer": json.dumps(answer or {"kind": "letters", "letters": "B"}),
    }


class AsyncMockBase(unittest.TestCase):
    pool = ["q1", "q2", "q3", "q4"]

    def setUp(self):
        self.fake = FakeDB()
        self.client = TestClient(self._app())
        chapters = [{"chapter_id": "optics", "title": "Optics", "subject": "Physics"}]
        pool = self.pool

        def fake_query(sql, params=None):
            if "GROUP BY" in sql:
                return [{"chapter_id": "optics", "n": len(pool)}] if pool else []
            skip = set(params.get("ex") or [])
            return [_q(c) for c in pool if c not in skip]

        patches = [
            patch("mock_test_agent.assembly.db", self.fake),
            patch("mock_test_agent.assembly.store.chapters", return_value=chapters),
            patch("mock_test_agent.assembly.store.query", side_effect=fake_query),
            patch("mock_test_agent.assembly.store.concept", return_value=None),
            patch("db.supabase_client.db", self.fake),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _app():
        app = FastAPI()
        app.include_router(router)
        return app

    def build(self, n=3, async_build=True, user=USER):
        body = {"user_id": user, "subjects": [{"subject": "Physics", "chapters": ["optics"], "n_questions": n}],
                "async_build": async_build}
        return self.client.post("/api/v1/mock/build", json=body)

    def wait(self, tid, user=USER):
        for _ in range(100):
            st = self.client.get(f"/api/v1/mock/status/{tid}", params={"user_id": user}).json()
            if st["status"] != "building":
                return st
            time.sleep(0.02)
        self.fail("build never finished")


class TestAsyncBuild(AsyncMockBase):
    def test_returns_immediately_then_progresses_to_built(self):
        r = self.build(3)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "building")
        self.assertEqual(body["total_questions"], 3)
        st = self.wait(body["mock_test_id"])
        self.assertEqual(st["status"], "built")
        self.assertEqual((st["prepared"], st["total"]), (3, 3))
        self.assertEqual(st["by_subject"], [{"subject": "Physics", "prepared": 3, "total": 3}])
        self.assertIsNone(st["error"])
        self.assertEqual(st["shortfall"], [])

    def test_status_building_visible_before_workers_finish(self):
        with patch.object(assembly, "_background_worker", lambda build: None):
            tid = self.build(3).json()["mock_test_id"]
        st = self.client.get(f"/api/v1/mock/status/{tid}", params={"user_id": USER}).json()
        self.assertEqual(st["status"], "building")
        self.assertEqual(st["prepared"], 0)

    def test_shortfall_reported(self):
        self.pool = ["q1", "q2"]
        self.setUp()
        tid = self.build(5).json()["mock_test_id"]
        st = self.wait(tid)
        self.assertEqual(st["status"], "built")
        self.assertEqual(st["shortfall"], [{"subject": "Physics", "requested": 5, "got": 2}])

    def test_sync_build_keeps_old_shape(self):
        r = self.build(2, async_build=False).json()
        self.assertEqual(r["status"], "built")
        self.assertEqual(len(r["sections"][0]["questions"]), 2)
        self.assertIn("feasibility", r)

    def test_status_404_for_other_user(self):
        tid = self.build(2).json()["mock_test_id"]
        self.wait(tid)
        r = self.client.get(f"/api/v1/mock/status/{tid}", params={"user_id": "someone-else"})
        self.assertEqual(r.status_code, 404)


class TestZeroQuestions(AsyncMockBase):
    pool = []

    def test_async_zero_questions_is_failed_not_built(self):
        r = self.build(3)
        # config check passes only because the picker found nothing at all
        tid = r.json()["mock_test_id"]
        st = self.wait(tid)
        self.assertEqual(st["status"], "failed")
        self.assertIn("No questions", st["error"])
        start = self.client.post("/api/v1/mock/start", json={"user_id": USER, "mock_test_id": tid})
        self.assertEqual(start.status_code, 409)

    def test_sync_zero_questions_is_400(self):
        r = self.build(3, async_build=False)
        self.assertEqual(r.status_code, 400)


class TestStartRules(AsyncMockBase):
    def test_start_refused_while_building(self):
        with patch.object(assembly, "_background_worker", lambda build: None):
            tid = self.build(3).json()["mock_test_id"]
        r = self.client.post("/api/v1/mock/start", json={"user_id": USER, "mock_test_id": tid})
        self.assertEqual(r.status_code, 409)
        self.assertIn("still being built", r.json()["detail"])

    def test_stale_building_reported_failed(self):
        with patch.object(assembly, "_background_worker", lambda build: None):
            tid = self.build(3).json()["mock_test_id"]
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        row = self.fake.tables["mock_tests"][0]
        row["created_at"] = old
        st = self.client.get(f"/api/v1/mock/status/{tid}", params={"user_id": USER}).json()
        self.assertEqual(st["status"], "failed")
        self.assertTrue(st["error"])

    def test_start_returns_questions_without_answers_and_is_idempotent(self):
        tid = self.build(3).json()["mock_test_id"]
        self.wait(tid)
        first = self.client.post("/api/v1/mock/start", json={"user_id": USER, "mock_test_id": tid}).json()
        self.assertEqual(len(first["questions"]), 3)
        self.assertTrue(all("answer" not in q for q in first["questions"]))
        cid = first["questions"][0]["content_id"]
        self.client.post("/api/v1/mock/answer", json={"user_id": USER, "mock_test_id": tid,
                                                      "content_id": cid, "choice": ["B"]})
        time.sleep(0.01)
        again = self.client.post("/api/v1/mock/start", json={"user_id": USER, "mock_test_id": tid}).json()
        self.assertEqual(again["started_at"], first["started_at"])
        self.assertEqual(again["answers"][0]["content_id"], cid)
        self.assertEqual(again["answers"][0]["choice"], ["B"])

    def test_answer_rejects_foreign_question_and_unstarted(self):
        tid = self.build(2).json()["mock_test_id"]
        self.wait(tid)
        body = {"user_id": USER, "mock_test_id": tid, "content_id": "q1", "choice": ["A"]}
        self.assertEqual(self.client.post("/api/v1/mock/answer", json=body).status_code, 409)
        self.client.post("/api/v1/mock/start", json={"user_id": USER, "mock_test_id": tid})
        body["content_id"] = "nope"
        self.assertEqual(self.client.post("/api/v1/mock/answer", json=body).status_code, 404)


class TestHistory(AsyncMockBase):
    def test_newest_first_owner_only_and_total(self):
        ids = []
        for _ in range(3):
            tid = self.build(2).json()["mock_test_id"]
            self.wait(tid)
            ids.append(tid)
            time.sleep(0.01)
        other = self.build(2, user="user-b").json()["mock_test_id"]
        self.wait(other, user="user-b")
        h = self.client.get("/api/v1/mock/history", params={"user_id": USER}).json()
        self.assertEqual(h["total"], 3)
        self.assertEqual([t["mock_test_id"] for t in h["tests"]], list(reversed(ids)))
        self.assertNotIn(other, [t["mock_test_id"] for t in h["tests"]])
        page = self.client.get("/api/v1/mock/history", params={"user_id": USER, "limit": 1, "offset": 1}).json()
        self.assertEqual([t["mock_test_id"] for t in page["tests"]], [ids[1]])

    def test_graded_has_score_and_in_progress_does_not(self):
        t1 = self.build(2).json()["mock_test_id"]
        self.wait(t1)
        qs = self.client.post("/api/v1/mock/start", json={"user_id": USER, "mock_test_id": t1}).json()["questions"]
        self.client.post("/api/v1/mock/answer", json={"user_id": USER, "mock_test_id": t1,
                                                      "content_id": qs[0]["content_id"], "choice": ["B"]})
        h = self.client.get("/api/v1/mock/history", params={"user_id": USER}).json()["tests"][0]
        self.assertEqual((h["status"], h["score"], h["attempted"]), ("in_progress", None, 1))
        sub = self.client.post("/api/v1/mock/submit", json={"user_id": USER, "mock_test_id": t1}).json()
        self.assertEqual(sub["overall"]["raw_score"], 3.0)
        h = self.client.get("/api/v1/mock/history", params={"user_id": USER}).json()["tests"][0]
        self.assertEqual((h["status"], h["score"], h["correct"], h["subjects"]), ("graded", 3.0, 1, ["Physics"]))


class TestGradingFixes(unittest.TestCase):
    def test_multiple_correct_partial_per_option(self):
        from mock_test_agent.grading import apply_marking_scheme, resolve_marking_scheme
        scheme = resolve_marking_scheme("JEE_ADVANCED")
        ans = {"kind": "letters", "letters": "ABD"}
        kw = dict(grade_status="incorrect", is_correct=False, question_type="multiple_correct",
                  answer=ans, scheme=scheme)
        self.assertEqual(apply_marking_scheme(student_choice=["A", "B"], **kw), 2.0)
        self.assertEqual(apply_marking_scheme(student_choice=["A", "C"], **kw), -2.0)

    def test_numeric_zero_is_graded_and_empty_is_unattempted(self):
        from mock_test_agent.grading import grade_submission, resolve_marking_scheme
        scheme = resolve_marking_scheme("JEE_ADVANCED")
        q = {"content_id": "n", "question_type": "numeric", "has_gradekey": True,
             "answer": {"kind": "integer", "value": 0.0}}
        r = grade_submission(question_row=q, submission={"numeric_value": 0.0}, scheme=scheme)
        self.assertEqual(r["grade_status"], "correct")
        r = grade_submission(question_row=q, submission={"student_answer": "", "choice": None}, scheme=scheme)
        self.assertEqual(r["grade_status"], "unattempted")
        self.assertEqual(r["answer_text"], "0")


if __name__ == "__main__":
    unittest.main()
