"""Agent identity: the Django access token, not the request, says whose data a call touches."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from services import auth

KEY = "test-signing-key-at-least-32-bytes-long"
ME = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _token(uid: Optional[str] = ME, *, token_type: str = "access", exp_in: int = 600, key: str = KEY) -> str:
    claims: Dict[str, Any] = {"token_type": token_type, "exp": int(time.time()) + exp_in, "user_id": 7}
    if uid:
        claims["supabase_user_id"] = uid
    return jwt.encode(claims, key, algorithm="HS256")


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class Body(BaseModel):
    user_id: str
    query: str = ""


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth.AgentAuthMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/chat/message")
    def chat(body: Body):
        return {"user_id": body.user_id}

    @app.post("/api/v1/study/message")
    def stream(body: Body):
        return StreamingResponse(iter([f"data: {body.user_id}\n\n"]), media_type="text/event-stream")

    @app.get("/api/v1/chat/sessions")
    def sessions(user_id: str):
        return {"user_id": user_id}

    @app.get("/api/v1/progress/{user_id}")
    def progress(user_id: str):
        return {"user_id": user_id}

    @app.put("/api/v1/plan/settings")
    def plan_settings(body: Body):
        return {"user_id": body.user_id}

    @app.post("/api/v1/classroom/assess/build")
    def assess(body: Dict[str, Any]):
        return body

    return app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth, "SIGNING_KEY", KEY)
    monkeypatch.setattr(auth, "SERVICE_TOKEN", "svc-secret")
    monkeypatch.setattr(auth, "limiter", auth.RateLimiter(per_minute=3, per_day=5))
    return TestClient(_app())


def test_auth_is_off_without_a_signing_key(monkeypatch):
    monkeypatch.setattr(auth, "SIGNING_KEY", "")
    res = TestClient(_app()).get("/api/v1/chat/sessions", params={"user_id": OTHER})
    assert res.status_code == 200
    assert res.json() == {"user_id": OTHER}


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "token",
    [
        None,
        _token(exp_in=-120),
        _token(key="a-different-signing-key-also-32-bytes"),
        _token(token_type="refresh"),
        _token(uid=None),
    ],
    ids=["missing", "expired", "bad-signature", "refresh-token", "no-claim"],
)
def test_rejects_anything_but_a_valid_access_token(client, token):
    res = client.get("/api/v1/chat/sessions", headers=_headers(token) if token else {})
    assert res.status_code == 401


def test_query_user_is_pinned_to_the_token(client):
    res = client.get("/api/v1/chat/sessions", headers=_headers(_token()))
    assert res.json() == {"user_id": ME}
    res = client.get("/api/v1/chat/sessions", params={"user_id": OTHER}, headers=_headers(_token()))
    assert res.status_code == 403


def test_path_user_must_be_the_caller(client):
    assert client.get(f"/api/v1/progress/{ME}", headers=_headers(_token())).json() == {"user_id": ME}
    assert client.get(f"/api/v1/progress/{OTHER}", headers=_headers(_token())).status_code == 403


def test_body_user_is_checked_and_filled(client):
    ok = client.post("/api/v1/chat/message", json={"user_id": ME, "query": "torque"}, headers=_headers(_token()))
    assert ok.json() == {"user_id": ME}
    other = client.post("/api/v1/chat/message", json={"user_id": OTHER, "query": "x"}, headers=_headers(_token()))
    assert other.status_code == 403
    filled = client.post("/api/v1/classroom/assess/build", json={"concept_id": "torque"}, headers=_headers(_token()))
    assert filled.json() == {"concept_id": "torque", "user_id": ME}


def test_plan_settings_is_a_route_not_a_user(client):
    res = client.put("/api/v1/plan/settings", json={"user_id": ME}, headers=_headers(_token()))
    assert res.status_code == 200


def test_streaming_reply_passes_through(client):
    res = client.post("/api/v1/study/message", json={"user_id": ME, "query": "hi"}, headers=_headers(_token()))
    assert res.status_code == 200
    assert res.text == f"data: {ME}\n\n"


def test_long_query_is_refused(client, monkeypatch):
    monkeypatch.setattr(auth, "MAX_QUERY_CHARS", 10)
    res = client.post("/api/v1/chat/message", json={"user_id": ME, "query": "x" * 11}, headers=_headers(_token()))
    assert res.status_code == 413


def test_service_token_is_trusted_for_server_calls(client):
    res = client.post(
        "/api/v1/classroom/assess/build",
        json={"user_id": OTHER},
        headers={"X-Agent-Service-Token": "svc-secret"},
    )
    assert res.json() == {"user_id": OTHER}
    wrong = client.post("/api/v1/classroom/assess/build", json={}, headers={"X-Agent-Service-Token": "nope"})
    assert wrong.status_code == 401


def test_model_turns_are_rate_limited_per_user(client):
    for _ in range(3):
        assert client.post("/api/v1/chat/message", json={"query": "q"}, headers=_headers(_token())).status_code == 200
    limited = client.post("/api/v1/chat/message", json={"query": "q"}, headers=_headers(_token()))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    # Reads do not count, and another learner has their own budget.
    assert client.get("/api/v1/chat/sessions", headers=_headers(_token())).status_code == 200
    assert client.post("/api/v1/chat/message", json={"query": "q"}, headers=_headers(_token(OTHER))).status_code == 200


def test_daily_budget_resets_the_next_day():
    limiter = auth.RateLimiter(per_minute=0, per_day=2)
    day = 1_700_000_000.0
    limiter.hit(ME, day)
    limiter.hit(ME, day + 1)
    with pytest.raises(auth.AuthError) as err:
        limiter.hit(ME, day + 2)
    assert err.value.status == 429
    limiter.hit(ME, day + 86400)


def test_production_refuses_to_start_without_a_key(monkeypatch):
    monkeypatch.setenv("AEROPT_ENV", "production")
    monkeypatch.setattr(auth, "SIGNING_KEY", "")
    with pytest.raises(RuntimeError):
        auth.check_production_config()


def test_api_server_installs_the_middleware_inside_cors():
    import api_server

    names = [m.cls.__name__ for m in api_server.app.user_middleware]
    assert names.index("CORSMiddleware") < names.index("AgentAuthMiddleware")
