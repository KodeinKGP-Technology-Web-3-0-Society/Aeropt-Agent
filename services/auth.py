"""Who is calling, and how much they may spend.

Django is the auth authority. Its SimpleJWT access tokens carry a
`supabase_user_id` claim (Aeropt-backend `users/views.py`), and that claim is
the only identity this service trusts. Every `user_id` a request names — query
string, URL path, or JSON body — must equal it, and is then overwritten with it,
so a route cannot be tricked into reading someone else's sessions.

Auth is on when `DJANGO_JWT_SIGNING_KEY` is set (production requires it, see
`check_production_config`). Unset, every request passes as before, which keeps
local development and the test suite unchanged.

Django's own server-to-server calls (classroom day specialists) send
`X-Agent-Service-Token` instead of a learner token.

The rate limiter is in-process, which is exact while the agent runs as a single
instance — the only way it is deployed.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode

import jwt

SIGNING_KEY = os.getenv("DJANGO_JWT_SIGNING_KEY", "").strip()
SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN", "").strip()
JWT_ALGORITHM = os.getenv("DJANGO_JWT_ALGORITHM", "HS256").strip() or "HS256"

TURNS_PER_MINUTE = int(os.getenv("AGENT_TURNS_PER_MINUTE", "10"))
TURNS_PER_DAY = int(os.getenv("AGENT_TURNS_PER_DAY", "60"))
MAX_QUERY_CHARS = int(os.getenv("AGENT_MAX_QUERY_CHARS", "4000"))
MAX_BODY_BYTES = int(os.getenv("AGENT_MAX_BODY_BYTES", str(1024 * 1024)))

# No learner token: liveness probes, voice endpoints, diagram SVGs, and the admin
# surface (which has its own AGENT_ADMIN_TOKEN gate, see services/admin_traces.py)
_PUBLIC = re.compile(r"^/(health|ready|api/v1/health|api/v1/voice/(?:transcribe|synthesize|tts|speakers)|api/v1/diagrams/[0-9a-f]{20}\.svg|admin/traces|api/v1/admin/.+)$")

# Routes that name the learner in the path. `/plan/settings` is a literal route.
_PATH_USER = re.compile(
    r"^/api/v1/(?:study/overview|study/history|progress|plan)/(?P<uid>[^/]+)$"
)

# Routes that run a model call and so spend the shared Groq quota.
_SPENDS_MODEL = frozenset({
    "/api/v1/chat/message",
    "/api/v1/study/message",
    "/api/v1/study/start",
    "/api/v1/study/advance",
    "/api/v1/sandbox/execute",
    "/api/v1/onboarding/diagnostic",
    "/api/v1/coach/run",
    "/api/v1/classroom/assess/grade",
})


def auth_required() -> bool:
    return bool(SIGNING_KEY)


def check_production_config() -> None:
    """Refuse to start a production agent that would serve anyone's data to anyone."""
    if os.getenv("AEROPT_ENV", "").strip().lower() != "production":
        return
    if not SIGNING_KEY:
        raise RuntimeError("AEROPT_ENV=production needs DJANGO_JWT_SIGNING_KEY (Django's JWT signing key).")


class AuthError(Exception):
    def __init__(self, status: int, detail: str, headers: Optional[Dict[str, str]] = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = headers or {}


def verify_access_token(authorization: str) -> str:
    """Return the caller's Supabase user id, or raise AuthError(401)."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "Sign in to use the tutor.")
    try:
        claims = jwt.decode(
            token.strip(),
            SIGNING_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp"]},
            leeway=30,
        )
    except jwt.ExpiredSignatureError:
        raise AuthError(401, "Your session expired. Please sign in again.")
    except jwt.InvalidTokenError:
        raise AuthError(401, "Sign in to use the tutor.")
    if claims.get("token_type") != "access":
        raise AuthError(401, "Sign in to use the tutor.")
    uid = str(claims.get("supabase_user_id") or "").strip()
    if not uid:
        # Tokens minted before the claim existed. Signing in again issues one with it.
        raise AuthError(401, "Please sign in again to continue.")
    return uid


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int):
        self.per_minute = per_minute
        self.per_day = per_day
        self._lock = threading.Lock()
        self._recent: Dict[str, Deque[float]] = defaultdict(deque)
        self._daily: Dict[str, Tuple[str, int]] = {}

    def hit(self, user_id: str, now: Optional[float] = None) -> None:
        """Count one model-spending request, or raise AuthError(429)."""
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self._lock:
            recent = self._recent[user_id]
            while recent and now - recent[0] >= 60:
                recent.popleft()
            if self.per_minute > 0 and len(recent) >= self.per_minute:
                retry = max(1, int(60 - (now - recent[0])) + 1)
                raise AuthError(
                    429,
                    "You're sending messages quickly. Take a breath and try again in a minute.",
                    {"Retry-After": str(retry)},
                )
            seen_day, count = self._daily.get(user_id, (day, 0))
            if seen_day != day:
                count = 0
            if self.per_day > 0 and count >= self.per_day:
                raise AuthError(
                    429,
                    "You've reached today's tutor limit. It resets at 05:30 IST.",
                    {"Retry-After": str(int(86400 - now % 86400))},
                )
            recent.append(now)
            self._daily[user_id] = (day, count + 1)


limiter = RateLimiter(TURNS_PER_MINUTE, TURNS_PER_DAY)

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]


async def _reply(send: Send, status: int, detail: str, headers: Optional[Dict[str, str]] = None) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive: Receive) -> bytes:
    chunks = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise AuthError(400, "The request was cut off.")
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise AuthError(413, "That request is too large.")
        chunks.append(chunk)
        if not message.get("more_body"):
            return b"".join(chunks)


def _bind_body(body: bytes, uid: str) -> bytes:
    """Check and pin `user_id` in a JSON object body; enforce the query cap."""
    if not body.strip():
        return body
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body
    named = data.get("user_id")
    if named not in (None, "") and str(named) != uid:
        raise AuthError(403, "That belongs to another account.")
    query = data.get("query")
    if isinstance(query, str) and len(query) > MAX_QUERY_CHARS:
        raise AuthError(413, f"Please keep a message under {MAX_QUERY_CHARS} characters.")
    data["user_id"] = uid
    return json.dumps(data).encode("utf-8")


class AgentAuthMiddleware:
    """Pure ASGI, so streamed (SSE) responses pass through untouched."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or not auth_required()
            or _PUBLIC.match(scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        service = headers.get(b"x-agent-service-token", b"").decode("latin-1")
        if SERVICE_TOKEN and service and hmac.compare_digest(service, SERVICE_TOKEN):
            await self.app(scope, receive, send)
            return

        try:
            uid = verify_access_token(headers.get(b"authorization", b"").decode("latin-1"))
            scope = self._bind_scope(scope, uid)
            method = scope["method"]
            if method == "POST" and scope["path"] in _SPENDS_MODEL:
                limiter.hit(uid)
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                body = _bind_body(await _read_body(receive), uid)
                scope = self._with_length(scope, len(body))
                receive = self._replay(body, receive)
        except AuthError as err:
            await _reply(send, err.status, err.detail, err.headers)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _bind_scope(scope: Scope, uid: str) -> Scope:
        match = _PATH_USER.match(scope["path"])
        if match and match.group("uid") != "settings" and match.group("uid") != uid:
            raise AuthError(403, "That belongs to another account.")
        pairs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        if any(k == "user_id" and v and v != uid for k, v in pairs):
            raise AuthError(403, "That belongs to another account.")
        pairs = [(k, v) for k, v in pairs if k != "user_id"] + [("user_id", uid)]
        state = dict(scope.get("state") or {})
        state["user_id"] = uid
        return {**scope, "query_string": urlencode(pairs).encode("latin-1"), "state": state}

    @staticmethod
    def _with_length(scope: Scope, length: int) -> Scope:
        raw = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"content-length"]
        raw.append((b"content-length", str(length).encode()))
        return {**scope, "headers": raw}

    @staticmethod
    def _replay(body: bytes, receive: Receive) -> Receive:
        sent = False

        async def replay() -> Dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return replay
