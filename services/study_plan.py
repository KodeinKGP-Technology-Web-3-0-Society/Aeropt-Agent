"""The exam plan: what to study each day from now until the JEE, and whether
the student is on pace. Deterministic — no model call.

Inputs are the student's own settings (exam date, minutes a day, study days a
week, weeks kept for revision — the browser passes them from the Django
profile, which the agent must not read) and their record: syllabus topic
statuses from services/study_overview.py and due reviews from the ledger.

It simulates study days forward:
  * each day starts with reviews that are due (spaced: every topic finished in
    the plan comes back a week later for 15 minutes),
  * then continues topics already started, then new topics,
  * new work alternates Physics, Chemistry and Mathematics so no subject waits
    until another is finished; inside a subject, syllabus order is unchanged,
  * until the day's minutes are used.
That yields this week's schedule, the date each unit will be finished, when the
whole syllabus will be done, and so the pace verdict and the daily minutes that
would actually be needed. Because it is recomputed from the record on every
request, the plan moves as the student studies — nothing to mark done by hand.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from services.study_overview import build_overview

# Minutes a topic takes, by where the student is with it.
MIN_NEW = 40          # learn + examples + a few practice questions
MIN_STARTED = 25      # finish what was begun
MIN_TUTOR_TAUGHT = 25 # no question bank: shorter
MIN_REVIEW = 15
REVIEW_AFTER_DAYS = 7
HORIZON_DAYS = 900
SUBJECT_ORDER = ("Physics", "Chemistry", "Mathematics")

# Approximate session dates (NTA publishes the exact ones each year).
EXAM_DEFAULTS = {
    "JEE_MAINS": (1, 22, "JEE Main (session 1)"),
    "JEE_ADVANCED": (5, 23, "JEE Advanced"),
}


def _subject_rank(subject: str) -> tuple:
    try:
        return (SUBJECT_ORDER.index(subject), subject)
    except ValueError:
        return (len(SUBJECT_ORDER), subject)


def interleave_subjects(queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Started topics first, then new ones. Each band alternates subjects.

    Topics stay in syllabus order inside a subject. Across subjects the plan
    takes one topic from each before returning to the first, so Physics is not
    finished before Chemistry and Mathematics begin.
    """
    started = [item for item in queue if item.get("kind") == "continue"]
    fresh = [item for item in queue if item.get("kind") != "continue"]
    return _round_robin(started) + _round_robin(fresh)


def _round_robin(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        subject = str(item.get("subject") or "Syllabus")
        buckets.setdefault(subject, []).append(item)
    order = sorted(buckets, key=_subject_rank)
    out: List[Dict[str, Any]] = []
    while any(buckets[subject] for subject in order):
        for subject in order:
            if buckets[subject]:
                out.append(buckets[subject].pop(0))
    return out


def _unit_key(unit: Dict[str, Any]) -> tuple:
    return (str(unit.get("subject") or "Syllabus"), unit["unit"])


def parallel_milestones(
    units: List[Dict[str, Any]],
    unit_finish: Dict[tuple, date],
) -> List[Dict[str, Any]]:
    """One milestone per remaining step, with the next unfinished unit of each subject.

    Units already mastered are absent from unit_finish, so the first milestone
    is whatever each subject still has left, not the first chapter of the book.
    """
    by_subject: Dict[str, List[Dict[str, Any]]] = {}
    for unit in units:
        key = _unit_key(unit)
        if key not in unit_finish:
            continue
        by_subject.setdefault(key[0], []).append(unit)
    subjects = sorted(by_subject, key=_subject_rank)
    depth = max((len(rows) for rows in by_subject.values()), default=0)
    milestones: List[Dict[str, Any]] = []
    for index in range(depth):
        parts: List[Dict[str, Any]] = []
        dates: List[date] = []
        for subject in subjects:
            rows = by_subject[subject]
            if index >= len(rows):
                continue
            unit = rows[index]
            when = unit_finish[_unit_key(unit)]
            parts.append({"subject": subject, "unit": unit["unit"], "by": when})
            dates.append(when)
        if not parts:
            continue
        when = max(dates)
        milestones.append({
            "month": when.strftime("%Y-%m"),
            "by": when,
            "units": [part["unit"] for part in parts],
            "subjects": parts,
        })
    return milestones


def subject_board(
    units: List[Dict[str, Any]],
    unit_finish: Dict[tuple, date],
) -> Dict[str, Any]:
    """One figure for parallel coverage: how far the furthest-behind subject has reached.

    A subject counts as covered by topics that are started or mastered. The
    together percent is the lowest of those shares, so the plan cannot look
    ahead while one subject is left behind.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for unit in units:
        grouped.setdefault(str(unit.get("subject") or "Syllabus"), []).append(unit)
    subjects = []
    for subject in sorted(grouped, key=_subject_rank):
        rows = grouped[subject]
        total = sum(int(unit.get("total") or 0) for unit in rows)
        mastered = sum(int(unit.get("mastered") or 0) for unit in rows)
        started = sum(int(unit.get("started") or 0) for unit in rows)
        done_units = [unit for unit in rows if unit.get("total") and unit.get("mastered") == unit.get("total")]
        remaining = [unit for unit in rows if _unit_key(unit) in unit_finish]
        finish = max((unit_finish[_unit_key(unit)] for unit in remaining), default=None)
        subjects.append({
            "subject": subject,
            "topics_total": total,
            "topics_mastered": mastered,
            "topics_started": started,
            "units_total": len(rows),
            "units_done": len(done_units),
            "next_unit": remaining[0]["unit"] if remaining else None,
            "finish_by": finish,
            "percent": round(100 * started / total) if total else 0,
        })
    scored = [row for row in subjects if row["topics_total"]]
    if not scored:
        return {"percent": 0, "behind": None, "level": True, "subjects": subjects}
    behind = min(scored, key=lambda row: (row["percent"], _subject_rank(row["subject"])))
    percents = {row["percent"] for row in scored}
    return {
        "percent": behind["percent"],
        "behind": behind["subject"],
        "level": len(percents) == 1,
        "subjects": subjects,
    }


def default_exam_date(exam: str, year: int) -> tuple[date, str]:
    """The first exam a student sits: JEE Main for "BOTH"."""
    key = "JEE_ADVANCED" if exam == "JEE_ADVANCED" else "JEE_MAINS"
    month, day, label = EXAM_DEFAULTS[key]
    return date(year, month, day), label


def _rest_weekdays(study_days: int) -> set:
    """Days off, Sunday first: 6 study days = Sunday off, 5 = weekend off."""
    order = [6, 5, 2, 3, 4, 1, 0]
    return set(order[: max(0, 7 - study_days)])


def build_plan(
    user_id: str,
    *,
    exam: str = "BOTH",
    exam_date: Optional[date] = None,
    target_year: Optional[int] = None,
    daily_minutes: int = 60,
    study_days: int = 6,
    revision_weeks: int = 6,
    tz_name: Optional[str] = None,
) -> Dict[str, Any]:
    from services.progress_report import resolve_tz

    tz = resolve_tz(tz_name)
    today = datetime.now(timezone.utc).astimezone(tz).date()
    daily_minutes = max(15, min(int(daily_minutes or 60), 600))
    study_days = max(1, min(int(study_days or 6), 7))
    revision_weeks = max(0, min(int(revision_weeks or 0), 26))

    exam_label = "JEE"
    if exam_date is None:
        year = target_year or (today.year + (1 if today.month >= 5 else 0))
        exam_date, exam_label = default_exam_date(exam, year)
        exam_date_estimated = True
    else:
        exam_label = default_exam_date(exam, exam_date.year)[1]
        exam_date_estimated = False
    revision_start = max(today, exam_date - timedelta(weeks=revision_weeks))

    overview = build_overview(user_id, tz_name=tz_name)
    units = overview["syllabus"]

    # ── today: what was already done ──
    from services.progress_report import build_report

    report = build_report(user_id, tz_name=tz_name, days=7)
    today_row = next((d for d in report["daily"] if d["date"] == today.isoformat()), None)
    minutes_today = int(today_row["minutes"]) if today_row else 0
    touched_today = {
        t["concept_id"] for t in report["timeline"]
        if t.get("concept_id") and _local_date(t.get("when"), tz) == today
    }

    # ── the queue ──
    queue: List[Dict[str, Any]] = []
    total_topics = sum(u["total"] for u in units)
    mastered = sum(u["mastered"] for u in units)
    for unit_ord, u in enumerate(units):
        for t in u["topics"]:
            if t["status"] in ("mastered", "review"):
                continue
            minutes = MIN_STARTED if t["status"] == "started" else (MIN_TUTOR_TAUGHT if t["tutor_taught"] else MIN_NEW)
            queue.append({
                "concept_id": t["concept_id"],
                "topic": t["topic"],
                "unit": u["unit"],
                "subject": u.get("subject") or "Syllabus",
                "unit_ord": unit_ord,
                "kind": "continue" if t["status"] == "started" else "learn",
                "minutes": minutes,
                "tutor_taught": t["tutor_taught"],
            })
    # Finish what is started before opening new topics. Both bands alternate subjects.
    queue = interleave_subjects(queue)

    reviews_due: List[Dict[str, Any]] = []
    for r in report["reviews"]:
        due = _local_date(r.get("due_at"), tz) or today
        reviews_due.append({"concept_id": r["concept_id"], "topic": r["name"], "due": max(due, today),
                            "kind": "review", "minutes": MIN_REVIEW})
    # Topics in "needs review" without a scheduled review still get one, now.
    scheduled = {r["concept_id"] for r in reviews_due}
    for g in overview["needs_review"]:
        for c in g["concepts"]:
            if c["concept_id"] not in scheduled:
                reviews_due.append({"concept_id": c["concept_id"], "topic": c["name"], "due": today,
                                    "kind": "review", "minutes": MIN_REVIEW})

    remaining_minutes = sum(q["minutes"] for q in queue) + sum(r["minutes"] for r in reviews_due)

    # ── simulate ──
    rest = _rest_weekdays(study_days)
    days: List[Dict[str, Any]] = []
    unit_finish: Dict[tuple, date] = {}
    pending_reviews = sorted(reviews_due, key=lambda r: r["due"])
    qi = 0
    day = today
    finish_date: Optional[date] = today if not queue else None
    while day <= today + timedelta(days=HORIZON_DAYS):
        entry: Dict[str, Any] = {"date": day, "rest": day.weekday() in rest, "items": [], "minutes": 0}
        if not entry["rest"]:
            budget = daily_minutes - (minutes_today if day == today else 0)
            for r in [r for r in pending_reviews if r["due"] <= day]:  # reviews first
                if budget < r["minutes"] and entry["items"]:
                    break
                pending_reviews.remove(r)
                budget -= r["minutes"]
                entry["items"].append(dict(r))
            while qi < len(queue) and (budget >= queue[qi]["minutes"] * 0.5 or not entry["items"]):
                q = queue[qi]
                entry["items"].append(dict(q))
                budget -= q["minutes"]
                unit_finish[(q.get("subject") or "Syllabus", q["unit"])] = day
                # Spaced repetition: everything learnt comes back a week later.
                pending_reviews.append({"concept_id": q["concept_id"], "topic": q["topic"],
                                        "due": day + timedelta(days=REVIEW_AFTER_DAYS),
                                        "kind": "review", "minutes": MIN_REVIEW, "spaced": True})
                qi += 1
            entry["minutes"] = sum(i["minutes"] for i in entry["items"])
            if qi >= len(queue) and finish_date is None:
                finish_date = day
        if len(days) < 7:
            days.append(entry)
        elif finish_date is not None:
            break
        day += timedelta(days=1)

    # Today: what the student has already done counts, and shows as done.
    if days and days[0]["date"] == today:
        done_today = []
        for cid in touched_today:
            name = next((t["topic"] for u in units for t in u["topics"] if t["concept_id"] == cid), None)
            if name:
                done_today.append({"concept_id": cid, "topic": name, "kind": "done", "minutes": 0, "done": True})
        days[0]["items"] = done_today + [i for i in days[0]["items"] if i["concept_id"] not in touched_today]
        days[0]["minutes_done"] = minutes_today

    # ── pace ──
    study_days_until_revision = sum(
        1 for k in range((revision_start - today).days)
        if (today + timedelta(days=k)).weekday() not in rest
    )
    needed = (
        int(math.ceil(remaining_minutes / study_days_until_revision / 5.0) * 5)
        if study_days_until_revision else None
    )
    if remaining_minutes == 0:
        verdict = "done"
    elif finish_date and finish_date <= revision_start:
        verdict = "on_track"
    elif finish_date and finish_date <= exam_date:
        verdict = "tight"
    else:
        verdict = "behind"

    milestones = parallel_milestones(units, unit_finish)
    together = subject_board(units, unit_finish)

    roadmap = []
    for u in units:
        roadmap.append({
            "subject": u.get("subject") or "Syllabus",
            "unit": u["unit"],
            "total": u["total"],
            "mastered": u["mastered"],
            "started": u["started"],
            "finish_by": unit_finish.get(_unit_key(u)),
            "complete": u["mastered"] == u["total"],
            "topics": u["topics"],
        })

    return {
        "settings": {
            "exam": exam,
            "exam_label": exam_label,
            "exam_date": exam_date,
            "exam_date_estimated": exam_date_estimated,
            "daily_minutes": daily_minutes,
            "study_days": study_days,
            "revision_weeks": revision_weeks,
        },
        "today": today,
        "days_left": (exam_date - today).days,
        "revision_start": revision_start,
        "topics_total": total_topics,
        "topics_mastered": mastered,
        "topics_remaining": len(queue),
        "reviews_pending": len(reviews_due),
        "remaining_minutes": remaining_minutes,
        "finish_date": finish_date,
        "verdict": verdict,
        "minutes_needed": needed,
        "week": [
            {
                "date": d["date"],
                "rest": d["rest"],
                "minutes": d["minutes"],
                "minutes_done": d.get("minutes_done", 0),
                "items": [
                    {k: v for k, v in i.items() if k in ("concept_id", "topic", "unit", "subject", "kind", "minutes", "tutor_taught", "done", "spaced")}
                    for i in d["items"]
                ],
            }
            for d in days[:7]
        ],
        "milestones": milestones,
        "together": together,
        "roadmap": roadmap,
    }


def _local_date(value: Any, tz: ZoneInfo) -> Optional[date]:
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()
