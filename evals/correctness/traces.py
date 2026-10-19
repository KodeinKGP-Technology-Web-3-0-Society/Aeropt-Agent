"""Count what real turns actually did: stops, checker skips, empty retrieval, fallbacks.

Reads only local files and a local database. A hosted URL is refused.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
FORCED_STOPS = {"max_steps", "time_budget", "call_cap"}


def host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def is_local_url(url: str) -> bool:
    host = host_of(url)
    return not host or host in LOCAL_HOSTS


def summarize_turns(turns: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one dict per turn. Missing fields count as unknown, not as zero events."""
    rows = list(turns)
    stops: Counter[str] = Counter()
    checkers: Counter[str] = Counter()
    fallbacks = 0
    retrieve_calls = 0
    retrieve_empty = 0
    forced = 0
    for row in rows:
        stop = str(row.get("stop_reason") or "unknown")
        stops[stop] += 1
        if stop in FORCED_STOPS:
            forced += 1
        verdict = str(row.get("checker_verdict") or "none")
        checkers[verdict] += 1
        if row.get("fallback"):
            fallbacks += 1
        if row.get("retrieve_called"):
            retrieve_calls += 1
            if row.get("retrieve_empty"):
                retrieve_empty += 1
    total = len(rows)
    return {
        "turns": total,
        "stop_reason": dict(stops),
        "forced_close": forced,
        "forced_close_rate": (forced / total) if total else 0.0,
        "checker_verdict": dict(checkers),
        "checker_skipped": sum(n for name, n in checkers.items() if name.startswith("skipped")),
        "fallback_turns": fallbacks,
        "retrieve_calls": retrieve_calls,
        "retrieve_empty": retrieve_empty,
    }


def _step_names(steps: Any) -> list[str]:
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError:
            return []
    if not isinstance(steps, list):
        return []
    names = []
    for step in steps:
        if isinstance(step, dict) and step.get("kind") == "tool":
            names.append(str(step.get("name") or ""))
    return names


def turn_from_trace_row(row: dict[str, Any], primary_model: str) -> dict[str, Any]:
    """Shape one ``agent_trace`` row the way ``summarize_turns`` expects."""
    metrics = row.get("metrics") or {}
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except ValueError:
            metrics = {}
    stop = str(metrics.get("stop_reason") or row.get("stop_reason") or "unknown")
    model = str(row.get("model_used") or "")
    names = _step_names(row.get("steps"))
    empty = False
    called = "retrieve_content" in names
    if called:
        blob = json.dumps(row.get("steps"), default=str).lower()
        empty = "chunks_found\": 0" in blob or "chunks_found': 0" in blob or "no chunk" in blob
    return {
        "stop_reason": stop,
        "checker_verdict": row.get("checker_verdict") or "none",
        "fallback": bool(model and primary_model and model != primary_model),
        "retrieve_called": called,
        "retrieve_empty": empty,
        "model_used": model,
    }


def summarize_transcripts(root: Path) -> dict[str, Any]:
    """Read ``var/transcripts`` step dumps. Eval sessions are counted apart from real ones.

    The dump stores the loop step index, not ``stop_reason``. Step 5 is the sixth
    iteration, which is the closing pass under the default 6-step budget.
    """
    files = [p for p in root.glob("*/*.jsonl") if p.is_file()] if root.exists() else []
    real_steps: Counter[int] = Counter()
    eval_steps: Counter[int] = Counter()
    tools: Counter[str] = Counter()
    chunks: Counter[str] = Counter()
    real_sessions: set[str] = set()
    eval_sessions: set[str] = set()
    real_files = 0
    for path in files:
        session = path.parent.name
        is_eval = session.startswith("eval_") or session in {"s"}
        (eval_sessions if is_eval else real_sessions).add(session)
        try:
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        except ValueError:
            continue
        if not lines:
            continue
        last_step = max(int(row.get("step") or 0) for row in lines)
        (eval_steps if is_eval else real_steps)[last_step] += 1
        if is_eval:
            continue
        real_files += 1
        for message in lines[-1].get("messages") or []:
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    name = str((call.get("function") or {}).get("name") or "")
                    if name:
                        tools[name] += 1
            if message.get("role") == "tool" and message.get("name") == "retrieve_content":
                content = message.get("content")
                blob = content if isinstance(content, str) else json.dumps(content, default=str)
                match = re.search(r"chunks_found[\"']?\s*[:=]\s*(\d+)", blob)
                chunks[match.group(1) if match else "missing"] += 1
    return {
        "real_files": real_files,
        "real_sessions": len(real_sessions),
        "eval_files": sum(eval_steps.values()),
        "eval_sessions": len(eval_sessions),
        "real_max_step": {str(k): real_steps[k] for k in sorted(real_steps)},
        "reached_closing_pass": real_steps.get(5, 0),
        "real_tools": dict(tools),
        "retrieve_chunks_found": dict(chunks),
    }


def scan_local_files() -> dict[str, Any]:
    """What is on disk. Does not open a database."""
    api_log = ROOT / "api.log"
    transcripts = ROOT / "var" / "transcripts"
    transcript_files = list(transcripts.glob("**/*")) if transcripts.exists() else []
    log_text = api_log.read_text(encoding="utf-8", errors="replace") if api_log.exists() else ""
    return {
        "api_log_bytes": api_log.stat().st_size if api_log.exists() else 0,
        "api_log_lines": log_text.count("\n") + (1 if log_text else 0),
        "api_log_mentions_stop": log_text.lower().count("stop_reason"),
        "transcript_files": len([p for p in transcript_files if p.is_file()]),
    }


def fetch_local_traces(limit: int = 200) -> tuple[list[dict[str, Any]], str]:
    """Return (rows, note). Refuses a hosted URL and an unreachable local database."""
    import os

    url = os.getenv("RETRIEVAL_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    try:
        import config

        url = url or getattr(config, "RETRIEVAL_DATABASE_URL", "")
    except Exception:
        pass
    if not is_local_url(url):
        return [], f"refused hosted database host {host_of(url)!r}"
    if not url:
        return [], "no local database URL is set"
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        return [], f"psycopg2 unavailable: {exc}"
    try:
        conn = psycopg2.connect(url, connect_timeout=3)
    except Exception as exc:
        return [], f"local database unreachable: {exc.__class__.__name__}"
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                select checker_verdict, model_used, steps, metrics
                from public.agent_trace
                order by created_at desc
                limit %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()], "ok"
    except Exception as exc:
        return [], f"agent_trace query failed: {exc.__class__.__name__}"
    finally:
        conn.close()
