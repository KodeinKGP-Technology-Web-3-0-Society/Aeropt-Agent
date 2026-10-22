"""Admin view of what the tutor did, turn by turn.

Reads `agent_trace` (one row per turn: every llm step, tool call with args and
result, handoff and checker verdict) plus the session's `chat_turns`, so an
operator can follow a student's message through to the reply.

Not learner-facing, so it does not use the learner JWT. It is closed unless
`AGENT_ADMIN_TOKEN` is set, and then every data route needs that token in
`X-Admin-Token`. The page itself carries no data and is served without it.
"""

from __future__ import annotations

import hmac
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

from db.supabase_client import db

router = APIRouter()

_PAGE = Path(__file__).resolve().parent.parent / "static" / "admin_traces.html"

_LIST_COLUMNS = (
    "trace_id, user_id, session_id, turn_index, route, tool_calls, handoffs, "
    "checker_verdict, latency_ms, model_used, created_at, metrics"
)
_OVERVIEW_WINDOW = 500


def _admin_token() -> str:
    return os.getenv("AGENT_ADMIN_TOKEN", "").strip()


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    expected = _admin_token()
    if not expected:
        # Closed by default: do not even confirm the surface exists.
        raise HTTPException(status_code=404, detail="Not found")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Admin token required")


def _percentile(values: List[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))]


def _ts(value: Any) -> datetime:
    """Parse a timestamp; `chat_turns.created_at` has no zone but is stored in UTC."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _session_turns(session_ids: List[str], role: Optional[str] = None) -> List[Dict[str, Any]]:
    if not session_ids:
        return []
    query = (
        db.client.table("chat_turns")
        .select("session_id, role, content, turn_index, agent_used, created_at")
        .in_("session_id", session_ids)
    )
    if role:
        query = query.eq("role", role)
    return query.order("created_at").execute().data or []


def _student_turn(trace: Dict[str, Any], turns: List[Dict[str, Any]]) -> Optional[int]:
    """Index of the user turn that started this trace's turn.

    `agent_trace.turn_index` counts differently from `chat_turns.turn_index`, so the
    two cannot be joined on it. Prefer the recorded query; traces written before it
    was recorded fall back to time: the last user turn saved before the trace was.
    """
    wanted = ((trace.get("metrics") or {}).get("query") or "").strip()
    users = [i for i, t in enumerate(turns) if t.get("role") == "user"]
    if wanted:
        for i in reversed(users):
            if (turns[i].get("content") or "").strip() == wanted:
                return i
    ended = _ts(trace.get("created_at"))
    before = [i for i in users if _ts(turns[i].get("created_at")) <= ended]
    return before[-1] if before else None


def _reply_after(start: Optional[int], turns: List[Dict[str, Any]]) -> Optional[str]:
    """The assistant turn between this user turn and the next one, if it was saved."""
    if start is None:
        return None
    for nxt in turns[start + 1:]:
        if nxt.get("role") == "user":
            return None
        if nxt.get("role") == "assistant":
            return nxt.get("content")
    return None


@router.get("/admin/traces", include_in_schema=False)
def admin_page() -> FileResponse:
    if not _admin_token():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_PAGE, media_type="text/html")


@router.get("/api/v1/admin/traces", dependencies=[Depends(require_admin)])
def list_traces(
    limit: int = Query(50, ge=1, le=200),
    before: Optional[str] = Query(None, description="created_at cursor from the previous page"),
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    route: Optional[str] = None,
    verdict: Optional[str] = None,
    q: Optional[str] = Query(None, description="substring of the student's message"),
) -> Dict[str, Any]:
    try:
        query = db.client.table("agent_trace").select(_LIST_COLUMNS)
        if user_id:
            query = query.eq("user_id", user_id)
        if session_id:
            query = query.eq("session_id", session_id)
        if route:
            query = query.eq("route", route)
        if verdict:
            query = query.eq("checker_verdict", verdict)
        if q:
            query = query.ilike("metrics->>query", f"%{q}%")
        if before:
            query = query.lt("created_at", before)
        rows = query.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trace store unavailable: {exc}")
    _fill_missing_queries(rows)
    return {
        "traces": rows,
        "next_before": rows[-1]["created_at"] if len(rows) == limit else None,
    }


def _fill_missing_queries(rows: List[Dict[str, Any]]) -> None:
    """Recover the student's message for traces written before it was recorded."""
    missing = [r for r in rows if not (r.get("metrics") or {}).get("query")]
    if not missing:
        return
    try:
        turns = _session_turns(sorted({r["session_id"] for r in missing}), role="user")
    except Exception:
        return
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for t in turns:
        by_session.setdefault(str(t["session_id"]), []).append(t)
    for r in missing:
        session_turns = by_session.get(str(r["session_id"]), [])
        idx = _student_turn(r, session_turns)
        if idx is not None:
            r["metrics"] = {**(r.get("metrics") or {}), "query": session_turns[idx]["content"], "query_recovered": True}


@router.get("/api/v1/admin/traces/{trace_id}", dependencies=[Depends(require_admin)])
def get_trace(trace_id: str) -> Dict[str, Any]:
    try:
        found = db.client.table("agent_trace").select("*").eq("trace_id", trace_id).limit(1).execute().data
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trace store unavailable: {exc}")
    if not found:
        raise HTTPException(status_code=404, detail="Trace not found")
    trace = found[0]
    turns: List[Dict[str, Any]] = []
    try:
        turns = _session_turns([trace["session_id"]])
    except Exception:
        pass  # the trace is still useful without the transcript
    start = _student_turn(trace, turns)
    if start is not None and not (trace.get("metrics") or {}).get("query"):
        trace["metrics"] = {**(trace.get("metrics") or {}), "query": turns[start]["content"], "query_recovered": True}
    for i, t in enumerate(turns):
        t["this_turn"] = i == start
    return {"trace": trace, "reply": _reply_after(start, turns), "conversation": turns}


@router.get("/api/v1/admin/overview", dependencies=[Depends(require_admin)])
def overview() -> Dict[str, Any]:
    try:
        rows = (
            db.client.table("agent_trace")
            .select("route, tool_calls, checker_verdict, latency_ms, model_used, created_at, metrics")
            .order("created_at", desc=True)
            .limit(_OVERVIEW_WINDOW)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trace store unavailable: {exc}")
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    # Rows written before migration 020 have no metrics; averaging them in as zeros lies.
    metrics = [r["metrics"] for r in rows if r.get("metrics")]
    n = len(rows) or 1
    m = len(metrics) or 1
    return {
        "window": len(rows),
        "with_telemetry": len(metrics),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "avg_tool_calls": round(sum(r.get("tool_calls") or 0 for r in rows) / n, 2),
        "avg_llm_calls": round(sum(x.get("llm_calls") or 0 for x in metrics) / m, 2),
        "avg_input_tokens": round(sum(x.get("input_tokens") or 0 for x in metrics) / m),
        "avg_output_tokens": round(sum(x.get("output_tokens") or 0 for x in metrics) / m),
        "verdicts": dict(Counter(r.get("checker_verdict") or "none" for r in rows)),
        "routes": dict(Counter(r.get("route") or "?" for r in rows)),
        "stop_reasons": dict(Counter(x.get("stop_reason") or "?" for x in metrics)),
        "models": dict(Counter(r.get("model_used") or "?" for r in rows)),
        "per_day": dict(sorted(Counter((r.get("created_at") or "")[:10] for r in rows).items())),
    }
