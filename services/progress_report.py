"""A student's progress, derived from the learning ledger. Deterministic — no model.

Sources, all in the agent's `public` schema:
  learning_events       every attempt, tutor turn and study milestone (migration 012)
  user_concept_state    current proficiency per concept (the scoring maths lives
                        in agents/diagnostic.py; this only reads it)
  learner_readiness_scores, review_schedule, learner_memory, study_sessions

`build_report` feeds the /progress page; `progress_digest` is the few lines the
tutors get in their prompts, so they know the trajectory as well as the facts.

Days, streaks and "today" are in the student's timezone (passed from the
browser), not the server's — a 23:30 IST practice must count for that day.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
from db.supabase_client import db

logger = logging.getLogger("jee_tutor.progress_report")

ACTIVE_GAP_MIN = 15        # events closer than this are one continuous sitting
LONE_EVENT_MIN = 2         # credit for an isolated event
TREND_WINDOW_DAYS = 14
STALE_AFTER_DAYS = 14
DEFAULT_TZ = "Asia/Kolkata"

_ERROR_ADVICE = {
    "calculation": "slow down on the arithmetic and check units at the end",
    "conceptual": "go back to why the principle applies before reaching for a formula",
    "procedural": "write the steps out in order — the method is slipping, not the idea",
    "misread": "underline what is given and what is asked before starting",
}


# ── helpers ───────────────────────────────────────────────────────────────────


# Browsers still report some pre-2008 zone names; systems without the legacy
# tzdata links (Ubuntu 24.04 moved them to tzdata-legacy) cannot load them.
_LEGACY_TZ = {
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Dacca": "Asia/Dhaka",
    "US/Eastern": "America/New_York",
    "US/Pacific": "America/Los_Angeles",
}


def _tz(name: Optional[str]) -> ZoneInfo:
    """The student's zone; legacy aliases mapped; never raises."""
    for candidate in (name, _LEGACY_TZ.get(name or ""), DEFAULT_TZ):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    return ZoneInfo("UTC")


resolve_tz = _tz


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _concept_meta(concept_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [c for c in set(concept_ids) if c]
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from retrieval import store

        if store.index_exists():
            for row in store.concepts_by_ids(ids):
                out[row["concept_id"]] = {
                    "name": row.get("name") or row["concept_id"],
                    "chapter": row.get("chapter") or "Other",
                    "unit": row.get("unit") or "",
                }
    except Exception:
        logger.debug("concept meta lookup failed", exc_info=True)
    for cid in ids:
        out.setdefault(cid, {"name": cid.replace("_", " "), "chapter": "Other", "unit": ""})
    return out


def _question_texts(content_ids: Iterable[str]) -> Dict[str, str]:
    ids = [c for c in set(content_ids) if c]
    if not ids:
        return {}
    try:
        from retrieval import store

        if store.index_exists():
            return {r["content_id"]: r.get("core_text") or "" for r in store.chunks_by_ids(ids)}
    except Exception:
        pass
    try:
        rows = db.client.table("content").select("content_id, core_text").in_("content_id", ids).execute().data
        return {r["content_id"]: r.get("core_text") or "" for r in rows or []}
    except Exception:
        return {}


def _status(proficiency: float, attempts: int) -> str:
    if attempts == 0:
        return "new"
    if proficiency >= config.MASTERY_THRESHOLD and attempts >= 3:
        return "mastered"
    if proficiency >= config.CLEAR_WEAK_THRESHOLD:
        return "strong"
    if proficiency < config.WEAK_THRESHOLD and attempts >= 2:
        return "weak"
    return "building"


def _active_minutes(times: List[datetime]) -> float:
    if not times:
        return 0.0
    times = sorted(times)
    minutes = LONE_EVENT_MIN
    for prev, cur in zip(times, times[1:]):
        gap = (cur - prev).total_seconds() / 60
        minutes += gap if gap <= ACTIVE_GAP_MIN else LONE_EVENT_MIN
    return minutes


def _streaks(active_days: set, today: date) -> Tuple[int, int]:
    """(current, best). Current counts back from today, or from yesterday if
    today has no activity yet — a streak is not broken until the day ends."""
    if not active_days:
        return 0, 0
    best = run = 0
    prev: Optional[date] = None
    for d in sorted(active_days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    start = today if today in active_days else today - timedelta(days=1)
    current = 0
    while start in active_days:
        current += 1
        start -= timedelta(days=1)
    return current, best


# ── the report ────────────────────────────────────────────────────────────────


#: The home page asks for the progress report and the study overview at the same
#: moment, and both read the same rows. A short cache lets the second reuse the
#: first's load; ten seconds is stale enough to be invisible, fresh enough that a
#: just-finished session shows up on the next visit.
_LOAD_TTL_S = 10.0
_load_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_load_lock = threading.Lock()
_load_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="report-load")

#: The columns the report reads from learning_events. `payload` is the heavy one;
#: `select *` also drags back ids and bookkeeping nobody here looks at.
_EVENT_COLUMNS = (
    "kind, concept_id, content_id, created_at, error_type, outcome, payload, "
    "proficiency_before, proficiency_after, source"
)


def _read_events(user_id: str) -> List[Dict[str, Any]]:
    try:
        return (
            db.client.table("learning_events")
            .select(_EVENT_COLUMNS)
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception:
        logger.debug("learning events read failed", exc_info=True)
        return []


def _read_states(user_id: str) -> List[Dict[str, Any]]:
    try:
        return (
            db.client.table("user_concept_state")
            .select("concept_id, proficiency_score, attempts, correct, flagged_weak, error_type_dist, last_seen_at, updated_at")
            .eq("user_id", user_id)
            .gt("attempts", 0)
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def _read_reviews(user_id: str) -> List[Dict[str, Any]]:
    try:
        return (
            db.client.table("review_schedule")
            .select("review_id, concept_id, due_at, reason, status")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .order("due_at")
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def _read_memories(user_id: str) -> List[Dict[str, Any]]:
    try:
        return db.read_learner_memories(user_id, limit=12) or []
    except Exception:
        return []


_load_inflight: Dict[str, "Future[Dict[str, Any]]"] = {}


def _load(user_id: str) -> Dict[str, Any]:
    # Single flight: the home page fires progress, overview and plan together and
    # all three need this load. The first caller reads; the others wait for it
    # instead of repeating the same five round trips.
    now = time.monotonic()
    with _load_lock:
        hit = _load_cache.get(user_id)
        if hit and now - hit[0] < _LOAD_TTL_S:
            return hit[1]
        pending = _load_inflight.get(user_id)
        if pending is None:
            pending = _load_inflight[user_id] = Future()
            owner = True
        else:
            owner = False
    if not owner:
        return pending.result()
    try:
        data = _read_all(user_id)
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _load_lock:
            _load_inflight.pop(user_id, None)
    pending.set_result(data)
    return data


def _read_all(user_id: str) -> Dict[str, Any]:
    # Five independent reads: run them together so the page waits for the
    # slowest one, not the sum of all five.
    events = _load_pool.submit(_read_events, user_id)
    states = _load_pool.submit(_read_states, user_id)
    reviews = _load_pool.submit(_read_reviews, user_id)
    readiness = _load_pool.submit(db.read_readiness_scores, user_id)
    memories = _load_pool.submit(_read_memories, user_id)
    data = {
        "events": events.result(),
        "states": states.result(),
        "reviews": reviews.result(),
        "readiness": {r["concept_id"]: r for r in readiness.result() if r.get("concept_id")},
        "memories": memories.result(),
    }
    with _load_lock:
        if len(_load_cache) > 256:
            _load_cache.clear()
        _load_cache[user_id] = (time.monotonic(), data)
    return data


def build_report(user_id: str, *, tz_name: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    tz = _tz(tz_name)
    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    data = _load(user_id)
    # Copies: the rows below get per-call fields (_t, _d) that depend on the
    # timezone, and the loaded rows are shared with other requests.
    events = [dict(e) for e in data["events"]]
    for e in events:
        e["_t"] = _parse(e.get("created_at")) or now
        e["_d"] = e["_t"].astimezone(tz).date()

    attempts = [e for e in events if e["kind"] == "attempt"]
    turns = [e for e in events if e["kind"] == "tutor_turn"]

    # ── overview ──
    week_ago = today - timedelta(days=6)
    prev_week = (today - timedelta(days=13), today - timedelta(days=7))

    def acc(rows: List[Dict[str, Any]]) -> Optional[float]:
        return round(sum(r["outcome"] == "correct" for r in rows) / len(rows), 3) if rows else None

    this_week = [a for a in attempts if a["_d"] >= week_ago]
    last_week = [a for a in attempts if prev_week[0] <= a["_d"] <= prev_week[1]]
    active_days = {e["_d"] for e in events}
    streak, best_streak = _streaks(active_days, today)
    by_day: Dict[date, List[datetime]] = defaultdict(list)
    for e in events:
        by_day[e["_d"]].append(e["_t"])

    states = data["states"]
    concept_ids = (
        {s["concept_id"] for s in states}
        | {e["concept_id"] for e in events if e.get("concept_id")}
        | {r["concept_id"] for r in data["reviews"]}
    )
    meta = _concept_meta(concept_ids)

    completed_sessions = [e for e in events if e["kind"] == "study_completed"]
    overview = {
        "questions_attempted": len(attempts),
        "questions_correct": sum(a["outcome"] == "correct" for a in attempts),
        "accuracy": acc(attempts),
        "attempts_7d": len(this_week),
        "accuracy_7d": acc(this_week),
        "accuracy_prev_7d": acc(last_week),
        "questions_asked_7d": sum(1 for t in turns if t["_d"] >= week_ago),
        "active_minutes_7d": round(sum(_active_minutes(by_day[d]) for d in by_day if d >= week_ago)),
        "active_days_30d": sum(1 for d in active_days if d >= today - timedelta(days=29)),
        "streak_days": streak,
        "best_streak_days": best_streak,
        "active_today": today in active_days,
        "concepts_practised": len(states),
        "concepts_mastered": 0,  # filled below
        "study_sessions_completed": len(completed_sessions),
        "last_active": max((e["_t"] for e in events), default=None),
    }

    # ── daily series ──
    daily = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_attempts = [a for a in attempts if a["_d"] == d]
        daily.append({
            "date": d.isoformat(),
            "attempts": len(day_attempts),
            "correct": sum(a["outcome"] == "correct" for a in day_attempts),
            "turns": sum(1 for t in turns if t["_d"] == d),
            "minutes": round(_active_minutes(by_day.get(d, []))),
        })

    # ── concepts ──
    by_concept_attempts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for a in attempts:
        if a.get("concept_id"):
            by_concept_attempts[a["concept_id"]].append(a)
    last_touch: Dict[str, datetime] = {}
    for e in events:
        cid = e.get("concept_id")
        if cid and (cid not in last_touch or e["_t"] > last_touch[cid]):
            last_touch[cid] = e["_t"]
    review_by_concept = {r["concept_id"]: r for r in data["reviews"]}
    trend_since = now - timedelta(days=TREND_WINDOW_DAYS)

    concepts = []
    for s in states:
        cid = s["concept_id"]
        prof = float(s.get("proficiency_score") or 0)
        n = int(s.get("attempts") or 0)
        c = int(s.get("correct") or 0)
        # Trend: score now vs the score before the first attempt in the window.
        window = sorted(
            (a for a in by_concept_attempts.get(cid, []) if a["_t"] >= trend_since and a.get("proficiency_before") is not None),
            key=lambda a: a["_t"],
        )
        trend = round(prof - float(window[0]["proficiency_before"]), 3) if window else None
        status = _status(prof, n)
        touched = last_touch.get(cid) or _parse(s.get("last_seen_at") or s.get("updated_at"))
        review = review_by_concept.get(cid)
        concepts.append({
            "concept_id": cid,
            "name": meta[cid]["name"],
            "chapter": meta[cid]["chapter"],
            "unit": meta[cid]["unit"],
            "proficiency": round(prof, 3),
            "attempts": n,
            "correct": c,
            "accuracy": round(c / n, 3) if n else None,
            "trend": trend,
            "status": status,
            "flagged_weak": bool(s.get("flagged_weak")),
            "last_practised": touched,
            "days_since": (now - touched).days if touched else None,
            "readiness": (data["readiness"].get(cid) or {}).get("score"),
            "review_due": review.get("due_at") if review else None,
        })
    # Taught in chat or a lesson but never practised: no user_concept_state row, yet
    # the student did study it. Listed as "new" with no score, so the study page and
    # the tutor know it was covered without pretending it was assessed.
    practised = {c["concept_id"] for c in concepts}
    for e in turns:
        cid = e.get("concept_id")
        if not cid or cid in practised:
            continue
        practised.add(cid)
        touched = last_touch.get(cid)
        review = review_by_concept.get(cid)
        concepts.append({
            "concept_id": cid,
            "name": meta[cid]["name"],
            "chapter": meta[cid]["chapter"],
            "unit": meta[cid]["unit"],
            "proficiency": 0.0,
            "attempts": 0,
            "correct": 0,
            "accuracy": None,
            "trend": None,
            "status": "new",
            "discussed_only": True,
            "flagged_weak": False,
            "last_practised": touched,
            "days_since": (now - touched).days if touched else None,
            "readiness": (data["readiness"].get(cid) or {}).get("score"),
            "review_due": review.get("due_at") if review else None,
        })

    concepts.sort(key=lambda c: (c["last_practised"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    overview["concepts_mastered"] = sum(c["status"] == "mastered" for c in concepts)

    # ── chapters ── (practice-based: a concept only discussed has no score to average)
    chapters_acc: Dict[str, Dict[str, Any]] = {}
    for c in (c for c in concepts if c["attempts"]):
        ch = chapters_acc.setdefault(c["chapter"], {
            "chapter": c["chapter"], "unit": c["unit"], "concepts": 0, "attempts": 0, "correct": 0,
            "mastered": 0, "weak": 0, "_wprof": 0.0, "last_practised": None,
        })
        ch["concepts"] += 1
        ch["attempts"] += c["attempts"]
        ch["correct"] += c["correct"]
        ch["mastered"] += c["status"] == "mastered"
        ch["weak"] += c["status"] == "weak"
        ch["_wprof"] += c["proficiency"] * max(1, c["attempts"])
        if c["last_practised"] and (ch["last_practised"] is None or c["last_practised"] > ch["last_practised"]):
            ch["last_practised"] = c["last_practised"]
    chapters = []
    for ch in chapters_acc.values():
        weight = sum(c["attempts"] for c in concepts if c["chapter"] == ch["chapter"])
        ch["proficiency"] = round(ch.pop("_wprof") / weight, 3) if weight else 0.0
        ch["accuracy"] = round(ch["correct"] / ch["attempts"], 3) if ch["attempts"] else None
        chapters.append(ch)
    chapters.sort(key=lambda c: c["proficiency"])

    # ── mistakes ──
    wrong = [a for a in attempts if a["outcome"] == "wrong"]
    by_type = Counter((a.get("error_type") or "unclassified") for a in wrong)
    recent_wrong = sorted(wrong, key=lambda a: a["_t"], reverse=True)[:20]
    texts = _question_texts(a.get("content_id") for a in recent_wrong)
    # A mistake later answered correctly on the same question is "fixed".
    fixed = {
        (a.get("content_id")) for a in attempts
        if a["outcome"] == "correct" and a.get("content_id")
        and any(w.get("content_id") == a["content_id"] and w["_t"] < a["_t"] for w in wrong)
    }
    notebook = [{
        "concept_id": a.get("concept_id"),
        "concept_name": meta.get(a.get("concept_id") or "", {}).get("name") if a.get("concept_id") else None,
        "content_id": a.get("content_id"),
        "question": (texts.get(a.get("content_id") or "") or "")[:600],
        "student_answer": ((a.get("payload") or {}).get("student_answer") or "")[:300],
        "error_type": a.get("error_type"),
        "source": a.get("source"),
        "when": a["_t"],
        "fixed": a.get("content_id") in fixed,
    } for a in recent_wrong]

    # ── reviews ──
    reviews = [{
        "concept_id": r["concept_id"],
        "name": meta[r["concept_id"]]["name"],
        "due_at": r.get("due_at"),
        "overdue": bool(_parse(r.get("due_at")) and _parse(r.get("due_at")) < now),
        "reason": r.get("reason"),
    } for r in data["reviews"]]

    report = {
        "generated_at": now,
        "timezone": str(tz),
        "overview": overview,
        "daily": daily,
        "chapters": chapters,
        "concepts": concepts,
        "mistakes": {"by_type": dict(by_type), "total_wrong": len(wrong), "notebook": notebook},
        "reviews": reviews,
        "memories": [
            {"kind": m.get("kind"), "fact": m.get("fact")}
            for m in data["memories"] if m.get("kind") in ("strength", "misconception", "preference", "goal")
        ][:6],
        "timeline": _timeline(events, meta)[:30],
    }
    report["insights"] = _insights(report)
    return report


def _timeline(events: List[Dict[str, Any]], meta: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for e in sorted(events, key=lambda e: e["_t"], reverse=True):
        name = meta.get(e.get("concept_id") or "", {}).get("name") or (e.get("concept_id") or "").replace("_", " ")
        kind = e["kind"]
        pay = e.get("payload") or {}
        if kind == "attempt":
            delta = None
            if e.get("proficiency_before") is not None and e.get("proficiency_after") is not None:
                delta = round((e["proficiency_after"] - e["proficiency_before"]) * 100)
            text = f"{'Solved' if e['outcome'] == 'correct' else 'Missed'} a question on {name}"
            if e["outcome"] == "wrong" and e.get("error_type"):
                text += f" ({e['error_type']})"
            out.append({"kind": kind, "text": text, "when": e["_t"], "concept_id": e.get("concept_id"),
                        "outcome": e["outcome"], "delta_pct": delta, "source": e.get("source")})
        elif kind == "tutor_turn":
            q = (pay.get("query") or "").strip()
            if not name and not q:
                continue
            text = f"Asked about {name}" if name else "Talked with the tutor"
            out.append({"kind": kind, "text": text, "detail": q[:120], "when": e["_t"],
                        "concept_id": e.get("concept_id"), "source": e.get("source")})
        elif kind == "study_started":
            out.append({"kind": kind, "text": f"Started a study session on {name}", "when": e["_t"], "concept_id": e.get("concept_id")})
        elif kind == "study_completed":
            label = {"mastered": "mastered", "needs_review": "needs review"}.get(e.get("outcome") or "", "finished")
            served = pay.get("questions_served")
            score = f" — {pay.get('questions_correct')}/{served}" if served else ""
            out.append({"kind": kind, "text": f"Finished {name}: {label}{score}", "when": e["_t"],
                        "concept_id": e.get("concept_id"), "outcome": e.get("outcome")})
        elif kind == "review_done":
            out.append({"kind": kind, "text": f"Reviewed {name}", "when": e["_t"], "concept_id": e.get("concept_id")})
    # Collapse runs of chat turns on the same concept into one line.
    collapsed: List[Dict[str, Any]] = []
    for item in out:
        prev = collapsed[-1] if collapsed else None
        if (prev and item["kind"] == "tutor_turn" and prev["kind"] == "tutor_turn"
                and prev.get("concept_id") == item.get("concept_id")
                and abs((prev["when"] - item["when"]).total_seconds()) < 3600):
            prev["count"] = prev.get("count", 1) + 1
            continue
        collapsed.append(item)
    return collapsed


def _insights(r: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Plain-language observations, most actionable first. At most five."""
    o = r["overview"]
    out: List[Dict[str, Any]] = []
    concepts = r["concepts"]

    overdue = [x for x in r["reviews"] if x["overdue"]]
    if overdue:
        names = ", ".join(x["name"] for x in overdue[:2])
        out.append({"tone": "action", "text": f"Review due: {names}. A short session now locks it in.",
                    "concept_id": overdue[0]["concept_id"], "action": "review"})

    by_type = r["mistakes"]["by_type"]
    wrong = r["mistakes"]["total_wrong"]
    if wrong >= 3:
        top, count = max(by_type.items(), key=lambda kv: kv[1])
        if count / wrong >= 0.4 and top in _ERROR_ADVICE:
            out.append({"tone": "pattern", "text": f"{count} of your {wrong} mistakes are {top} errors — {_ERROR_ADVICE[top]}."})

    weak = sorted((c for c in concepts if c["status"] == "weak"), key=lambda c: c["proficiency"])
    if weak:
        c = weak[0]
        out.append({"tone": "action", "text": f"{c['name']} is your weakest area ({round(c['proficiency'] * 100)}%). Study it next.",
                    "concept_id": c["concept_id"], "action": "study"})

    stale = [c for c in concepts if c["status"] in ("mastered", "strong") and (c["days_since"] or 0) >= STALE_AFTER_DAYS]
    if stale:
        c = max(stale, key=lambda c: c["days_since"] or 0)
        out.append({"tone": "action", "text": f"You haven't touched {c['name']} in {c['days_since']} days — a quick refresh keeps it strong.",
                    "concept_id": c["concept_id"], "action": "study"})

    if o["accuracy_7d"] is not None and o["accuracy_prev_7d"] is not None:
        diff = round((o["accuracy_7d"] - o["accuracy_prev_7d"]) * 100)
        if abs(diff) >= 10:
            word = "up" if diff > 0 else "down"
            out.append({"tone": "good" if diff > 0 else "pattern",
                        "text": f"Accuracy this week is {word} {abs(diff)} points on last week ({round(o['accuracy_7d'] * 100)}%)."})

    improving = [c for c in concepts if (c["trend"] or 0) >= 0.15]
    if improving:
        c = max(improving, key=lambda c: c["trend"])
        out.append({"tone": "good", "text": f"{c['name']} is up {round(c['trend'] * 100)} points in the last two weeks."})

    # Ready for what comes next: a mastered concept whose dependents are untouched.
    try:
        from retrieval import store

        touched = {c["concept_id"] for c in concepts} | {
            t["concept_id"] for t in r["timeline"] if t.get("concept_id")
        }
        for c in (x for x in concepts if x["status"] == "mastered"):
            nxt = [d for d in store.dependents(c["concept_id"]) if d not in touched]
            if nxt:
                name = _concept_meta([nxt[0]])[nxt[0]]["name"]
                out.append({"tone": "action", "text": f"You've mastered {c['name']} — you're ready for {name}.",
                            "concept_id": nxt[0], "action": "study"})
                break
    except Exception:
        pass

    if o["streak_days"] >= 2:
        out.append({"tone": "good", "text": f"{o['streak_days']}-day streak. Keep it going today."
                    if not o["active_today"] else f"{o['streak_days']}-day streak — today counts."})
    elif o["questions_attempted"] == 0:
        out.append({"tone": "action", "text": "No questions answered yet. Start a study session to see your progress here.",
                    "action": "study"})
    return out[:5]


def progress_digest(user_id: str, *, tz_name: Optional[str] = None) -> str:
    """Four or five facts for a tutor's prompt. Empty when there is no history."""
    try:
        r = build_report(user_id, tz_name=tz_name, days=14)
    except Exception:
        logger.debug("progress digest failed", exc_info=True)
        return ""
    o = r["overview"]
    discussed = [c for c in r["concepts"] if c.get("discussed_only")][:5]
    if not o["questions_attempted"] and not o["study_sessions_completed"] and not discussed:
        return ""
    lines = []
    if o["accuracy"] is not None:
        week = f", {round(o['accuracy_7d'] * 100)}% this week" if o["accuracy_7d"] is not None else ""
        mastered_n = o["concepts_mastered"]
        lines.append(f"- Overall: {o['questions_correct']}/{o['questions_attempted']} questions right{week}; "
                     f"{mastered_n} concept{'' if mastered_n == 1 else 's'} mastered; "
                     f"{o['streak_days']}-day streak.")
    weak = [c for c in r["concepts"] if c["status"] == "weak"][:3]
    if weak:
        lines.append("- Weak right now: " + ", ".join(f"{c['name']} ({round(c['proficiency'] * 100)}%)" for c in weak) + ".")
    mastered = [c for c in r["concepts"] if c["status"] == "mastered"][:4]
    if mastered:
        lines.append("- Mastered: " + ", ".join(c["name"] for c in mastered) + ".")
    by_type = r["mistakes"]["by_type"]
    if r["mistakes"]["total_wrong"] >= 3:
        top, count = max(by_type.items(), key=lambda kv: kv[1])
        lines.append(f"- Most common mistake type: {top} ({count} of {r['mistakes']['total_wrong']}).")
    if r["reviews"]:
        lines.append("- Reviews pending: " + ", ".join(x["name"] for x in r["reviews"][:3]) + ".")
    if discussed:
        lines.append("- Studied with the tutor recently, not practised yet: " + ", ".join(c["name"] for c in discussed) + ".")
    return "\n".join(lines)
