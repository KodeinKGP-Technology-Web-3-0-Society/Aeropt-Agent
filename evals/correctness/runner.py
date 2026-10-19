"""Run the correctness set live, or rescore a saved run offline.

Live mode calls the tutor. User-state stays in the in-memory eval database, so
a run never writes to Supabase. Retrieval is allowed only when
``RETRIEVAL_DATABASE_URL`` points at localhost; any other URL is refused.

    python -m evals.runner --suite correctness --mode live
    python -m evals.runner --suite correctness --mode replay
    python -m evals.correctness.runner --mode live --limit 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.correctness.score import Problem, grade_response, load_problems
from evals.correctness.traces import (
    FORCED_STOPS,
    fetch_local_traces,
    scan_local_files,
    summarize_transcripts,
    summarize_turns,
    turn_from_trace_row,
)

RUNS_DIR = Path(__file__).resolve().parent / "runs"

# Retrieval is not the corpus. These strings mark an empty retrieve_content result.
_EMPTY_MARKS = ('"chunks_found": 0', "'chunks_found': 0", "chunks_found\": 0", "no chunk")


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (k - lo) * (ordered[hi] - ordered[lo])


class HostedRetrievalBlocked(RuntimeError):
    """A correctness run tried to open a non-local retrieval database."""


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def retrieval_is_local() -> bool:
    """True only when the retrieval URL is this machine. An empty URL is not local."""
    from urllib.parse import urlparse

    from retrieval.store import database_url

    host = (urlparse(database_url() or "").hostname or "").lower()
    return host in _LOCAL_HOSTS


def block_hosted():
    """In-memory user DB. Retrieval opens a socket only for a localhost URL."""
    import sys
    from contextlib import contextmanager
    from unittest.mock import patch

    from evals.fakes import InMemoryDatabase

    @contextmanager
    def _ctx():
        import db.supabase_client as supabase_client

        fake = InMemoryDatabase()
        if not hasattr(fake, "read_learner_memories"):
            fake.read_learner_memories = lambda *a, **k: []  # type: ignore[attr-defined]
        original = supabase_client.db
        supabase_client.db = fake
        rebound = []
        for module in list(sys.modules.values()):
            if module is None:
                continue
            if getattr(module, "db", None) is original:
                module.db = fake
                rebound.append(module)

        def _refuse(*_a, **_k):
            raise HostedRetrievalBlocked(
                "correctness eval refused the retrieval database (hosted data is off-limits, "
                "and local Postgres was not the configured URL)"
            )

        def _empty(state):
            return {
                "primary_concept_id": (state or {}).get("primary_concept_id") or "",
                "retrieved_chunks": [],
                "retrieval_confidence": "low",
                "retrieval_notes": [
                    "Correctness eval: no corpus chunks. Local retrieval is not in use, "
                    "and hosted retrieval is refused."
                ],
                "retrieval_decision": {"action": "retrieve", "reason": "eval_retrieval_blocked"},
                "retrieval_citations": [],
                "error": None,
            }

        try:
            if retrieval_is_local():
                yield fake
                return
            with (
                patch("retrieval.store._get_pool", _refuse),
                patch("retrieval.store.query", _refuse),
                patch("retrieval.store.index_exists", lambda: False),
                patch("retrieval.service.retrieve", _refuse),
                patch("retrieval.bridge.retrieve_for_state", _empty),
            ):
                yield fake
        finally:
            supabase_client.db = original
            for module in rebound:
                module.db = original

    return _ctx()


def _tool_names(trace: dict[str, Any]) -> list[str]:
    names = []
    for step in trace.get("steps") or []:
        if isinstance(step, dict) and step.get("kind") == "tool":
            names.append(str(step.get("name") or ""))
    return names


def _retrieve_empty(trace: dict[str, Any], names: list[str]) -> bool:
    if "retrieve_content" not in names:
        return False
    blob = json.dumps(trace.get("steps"), default=str)
    return any(mark in blob for mark in _EMPTY_MARKS) or "eval_retrieval_blocked" in blob


def run_problem(problem: Problem, fake_db: Any) -> dict[str, Any]:
    """One live tutor turn. ``fake_db`` collects the trace the turn persists."""
    from agents.learning_agent import learning_agent_turn

    before = len(fake_db.agent_traces)
    started = time.perf_counter()
    result = learning_agent_turn(
        {
            "user_id": f"eval_user_{problem.id}",
            "session_id": f"eval_sess_{problem.id}",
            "query": problem.student_message(),
            "recent_turns": [],
            "interaction_count": 1,
            "target_exam": "JEE_ADVANCED",
            "preferred_depth": "standard",
            "carry": {},
        }
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response = result.get("response") or ""
    grade = grade_response(response, problem.expect)
    trace = fake_db.agent_traces[-1] if len(fake_db.agent_traces) > before else {}
    metrics = trace.get("metrics") or {}
    names = _tool_names(trace)
    stop = str(result.get("agent_stop_reason") or metrics.get("stop_reason") or "unknown")
    model = str(result.get("model_used") or trace.get("model_used") or "")
    return {
        "id": problem.id,
        "subject": problem.subject,
        "topic": problem.topic,
        "source": problem.source,
        "status": grade.status,
        "method": grade.method,
        "extracted": grade.extracted,
        "numeric": grade.numeric,
        "expected": problem.expect.value if problem.expect.kind != "letters" else problem.expect.letters,
        "expect_kind": problem.expect.kind,
        "detail": grade.detail,
        "stop_reason": stop,
        "budget_forced": stop in FORCED_STOPS,
        "checker_verdict": result.get("checker_verdict"),
        "llm_calls": int(metrics.get("llm_calls") or 0),
        "tool_calls": names,
        "retrieve_called": "retrieve_content" in names,
        "retrieve_empty": _retrieve_empty(trace, names),
        "model_used": model,
        "latency_ms": int(trace.get("latency_ms") or elapsed_ms),
        "wall_ms": elapsed_ms,
        "response": response,
    }


def summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    passed = sum(1 for item in items if item.get("status") == "pass")
    wrong = sum(1 for item in items if item.get("status") == "wrong")
    unparsed = sum(1 for item in items if item.get("status") == "unparsed")
    latencies = [float(item.get("latency_ms") or 0) for item in items]
    turns = summarize_turns(items)
    by_subject: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = by_subject.setdefault(item.get("subject") or "?", {"pass": 0, "n": 0})
        bucket["n"] += 1
        if item.get("status") == "pass":
            bucket["pass"] += 1
    return {
        "n": total,
        "pass": passed,
        "wrong": wrong,
        "unparsed": unparsed,
        "pass_rate": (passed / total) if total else 0.0,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "by_subject": by_subject,
        **turns,
    }


def format_table(items: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        f"{'id':<42} {'status':<9} {'stop':<14} {'checker':<16} {'llm':>4} {'ms':>7} got",
        f"{'-' * 42} {'-' * 9} {'-' * 14} {'-' * 16} {'-' * 4} {'-' * 7} ---",
    ]
    for item in items:
        got = item.get("numeric")
        shown = f"{got:.6g}" if isinstance(got, float) else (item.get("extracted") or "—")[:24]
        lines.append(
            f"{item['id']:<42} {item['status']:<9} {str(item.get('stop_reason')):<14} "
            f"{str(item.get('checker_verdict') or ''):<16} {int(item.get('llm_calls') or 0):>4} "
            f"{int(item.get('latency_ms') or 0):>7} {shown}"
        )
    lines.append("-" * 100)
    lines.append(
        f"PASS {summary['pass']}/{summary['n']} ({summary['pass_rate']:.1%})  "
        f"wrong {summary['wrong']}  unparsed {summary['unparsed']}  "
        f"latency p50/p95 {summary['latency_p50_ms']:.0f}/{summary['latency_p95_ms']:.0f} ms  "
        f"forced close {summary['forced_close']}  checker skipped {summary['checker_skipped']}  "
        f"fallback {summary['fallback_turns']}"
    )
    return "\n".join(lines)


def select_problems(ids: Optional[list[str]], limit: Optional[int]) -> list[Problem]:
    problems = load_problems()
    if ids:
        wanted = set(ids)
        problems = [p for p in problems if p.id in wanted]
        missing = wanted - {p.id for p in problems}
        if missing:
            raise SystemExit(f"unknown problem id(s): {', '.join(sorted(missing))}")
    if limit is not None:
        problems = problems[:limit]
    return problems


def run_live(problems: list[Problem], out_path: Path) -> dict[str, Any]:
    import config

    items: list[dict[str, Any]] = []
    primary = getattr(config, "GEMINI_MODEL", "")
    with block_hosted() as fake:
        for index, problem in enumerate(problems, start=1):
            print(f"[{index}/{len(problems)}] {problem.id}", flush=True)
            try:
                item = run_problem(problem, fake)
            except Exception as exc:  # noqa: BLE001 — one problem must not drop the rest
                item = {
                    "id": problem.id,
                    "subject": problem.subject,
                    "topic": problem.topic,
                    "source": problem.source,
                    "status": "error",
                    "method": "none",
                    "extracted": "",
                    "numeric": None,
                    "expected": problem.expect.value,
                    "expect_kind": problem.expect.kind,
                    "detail": f"{exc.__class__.__name__}: {exc}"[:400],
                    "stop_reason": "error",
                    "budget_forced": False,
                    "checker_verdict": None,
                    "llm_calls": 0,
                    "tool_calls": [],
                    "retrieve_called": False,
                    "retrieve_empty": False,
                    "model_used": "",
                    "fallback": False,
                    "latency_ms": 0,
                    "wall_ms": 0,
                    "response": "",
                }
            item["fallback"] = bool(item.get("model_used") and primary and item["model_used"] != primary)
            items.append(item)
            _write_run(out_path, items, partial=True, primary_model=primary)
            if item.get("stop_reason") == "key_exhaustion":
                print("stopping: key exhaustion", flush=True)
                break
    return _write_run(out_path, items, partial=False, primary_model=primary)


def _write_run(path: Path, items: list[dict[str, Any]], partial: bool, primary_model: str) -> dict[str, Any]:
    summary = summarize_items(items)
    summary["errors"] = sum(1 for item in items if item.get("status") == "error")
    local = retrieval_is_local()
    payload = {
        "partial": partial,
        "primary_model": primary_model,
        "retrieval": "local" if local else "blocked",
        "retrieval_note": (
            "Retrieval used the local Postgres corpus. User-state stayed in memory."
            if local
            else (
                "Hosted retrieval was refused. Local Postgres was not the configured database, "
                "so these turns solved with no corpus chunks."
            )
        ),
        "summary": summary,
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def run_replay(path: Path) -> dict[str, Any]:
    """Rescore saved replies. Does not call the model."""
    if not path.exists():
        raise SystemExit(f"no saved run at {path}. Record one with --mode live.")
    saved = json.loads(path.read_text(encoding="utf-8"))
    by_id = {p.id: p for p in load_problems()}
    items = []
    for item in saved.get("items") or []:
        problem = by_id.get(item.get("id"))
        if problem is None:
            continue
        grade = grade_response(item.get("response") or "", problem.expect)
        rescored = dict(item)
        rescored["status"] = grade.status
        rescored["method"] = grade.method
        rescored["extracted"] = grade.extracted
        rescored["numeric"] = grade.numeric
        rescored["detail"] = grade.detail
        items.append(rescored)
    summary = summarize_items(items)
    return {"summary": summary, "items": items, "source": str(path)}


def latest_run() -> Optional[Path]:
    runs = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def trace_report(limit: int = 200) -> dict[str, Any]:
    import config

    files = scan_local_files()
    files["transcripts"] = summarize_transcripts(ROOT / "var" / "transcripts")
    rows, note = fetch_local_traces(limit)
    primary = getattr(config, "GEMINI_MODEL", "")
    summary = summarize_turns(turn_from_trace_row(row, primary) for row in rows)
    return {"files": files, "database_note": note, "traces": summary}


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Correctness evals for the tutor")
    parser.add_argument("--mode", choices=["live", "replay", "traces"], default="replay")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", nargs="*", default=None)
    parser.add_argument("--run", type=str, default=None, help="Saved run JSON for replay, or the live output path.")
    args = parser.parse_args(argv)

    if args.mode == "traces":
        report = trace_report()
        print(json.dumps(report, indent=2))
        return

    if args.mode == "replay":
        path = Path(args.run) if args.run else latest_run()
        if path is None:
            raise SystemExit(
                "no correctness run to replay. Record one with "
                "python -m evals.runner --suite correctness --mode live"
            )
        payload = run_replay(path)
        print(format_table(payload["items"], payload["summary"]))
        return

    problems = select_problems(args.ids, args.limit)
    if not problems:
        raise SystemExit("no problems selected")
    out = Path(args.run) if args.run else (RUNS_DIR / "baseline.json")
    payload = run_live(problems, out)
    print(format_table(payload["items"], payload["summary"]))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
