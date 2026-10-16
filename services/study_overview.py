"""What the /study landing page shows: where the student stands and what's next.

Three buckets instead of a flat "continue where you left off" list —
  mastered      done, and nothing says otherwise
  in_progress   started or practising, not there yet
  needs_review  a review is pending, the last session said so, it is weak, or a
                strong topic has gone untouched for two weeks
— plus an "up next" recommendation with its reason, and syllabus coverage in
curriculum order (chapters and concepts carry `ord` in the local index), so the
student sees what is left to cover, not just what they have touched.

Deterministic: built from services/progress_report.py, study_sessions and the
concept index. No model call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.supabase_client import db
from services.progress_report import STALE_AFTER_DAYS, build_report

logger = logging.getLogger("jee_tutor.study_overview")

_PHASE_WORD = {"learn": "Learn", "examples": "Examples", "practice": "Practice"}


def _index() -> Dict[str, Any]:
    """Chapters and in-syllabus concepts in curriculum order, from the retrieval database."""
    try:
        from retrieval import store

        if not store.index_exists():
            return {"chapters": [], "concepts": {}, "by_chapter": {}}
        chapters = store.chapters()
        concepts = {c["concept_id"]: c for c in store.all_concepts()}
    except Exception:
        logger.debug("concept index unavailable", exc_info=True)
        return {"chapters": [], "concepts": {}, "by_chapter": {}}
    by_chapter: Dict[str, List[Dict[str, Any]]] = {}
    for c in concepts.values():
        if str(c.get("in_syllabus")) in ("1", "True", "true"):
            by_chapter.setdefault(c.get("chapter_id") or "", []).append(c)
    for rows in by_chapter.values():
        rows.sort(key=lambda c: (int(c.get("ord") or 0), c["concept_id"]))
    chapters = [ch for ch in chapters if str(ch.get("in_syllabus")) in ("1", "True", "true") and by_chapter.get(ch["chapter_id"])]
    return {"chapters": chapters, "concepts": concepts, "by_chapter": by_chapter}


def _iso(value: Any) -> Optional[str]:
    """One timestamp format, so sorting by recency compares like with like."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _row(cid: str, idx: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    c = idx["concepts"].get(cid) or {}
    return {
        "concept_id": cid,
        "name": c.get("name") or cid.replace("_", " "),
        "chapter": c.get("chapter") or "Other",
        "chapter_id": c.get("chapter_id") or "",
        **extra,
    }


def _group(rows: List[Dict[str, Any]], idx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rows grouped by chapter, chapters in curriculum order."""
    order = {ch["chapter_id"]: i for i, ch in enumerate(idx["chapters"])}
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(r["chapter"], {"chapter": r["chapter"], "chapter_id": r["chapter_id"], "concepts": []})
        g["concepts"].append(r)
    return sorted(groups.values(), key=lambda g: order.get(g["chapter_id"], 999))


def build_overview(user_id: str, *, tz_name: Optional[str] = None) -> Dict[str, Any]:
    idx = _index()
    report = build_report(user_id, tz_name=tz_name, days=14)
    now = datetime.now(timezone.utc)

    try:
        sessions = db.list_user_study_sessions(user_id, limit=60)
    except Exception:
        sessions = []
    # Newest session per concept, and only sessions the student actually used.
    latest_session: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        used = s.get("outcome") or s.get("current_phase") not in (None, "learn") or int(s.get("questions_served") or 0) > 0
        if not used:
            continue
        latest_session.setdefault(s["concept_id"], s)
    started = {
        t["concept_id"] for t in report["timeline"] if t.get("kind") == "study_started" and t.get("concept_id")
    }
    for s in sessions:  # a session with messages counts as started even before a phase change
        if s["concept_id"] in started:
            latest_session.setdefault(s["concept_id"], s)

    progress = {c["concept_id"]: c for c in report["concepts"]}
    reviews = {r["concept_id"]: r for r in report["reviews"]}
    # Only real concepts: sessions from before topics were resolved stored raw
    # text ("LC oscillation", "concept 1") as the id.
    known = idx["concepts"]
    touched = {cid for cid in set(progress) | set(latest_session) | set(reviews) if not known or cid in known}

    mastered: List[Dict[str, Any]] = []
    in_progress: List[Dict[str, Any]] = []
    needs_review: List[Dict[str, Any]] = []
    for cid in touched:
        p = progress.get(cid) or {}
        s = latest_session.get(cid) or {}
        r = reviews.get(cid)
        status = p.get("status")
        stale = status in ("mastered", "strong") and (p.get("days_since") or 0) >= STALE_AFTER_DAYS
        base = _row(
            cid, idx,
            proficiency=p.get("proficiency"),
            attempts=p.get("attempts", 0),
            correct=p.get("correct", 0),
            last_practised=_iso(p.get("last_practised") or s.get("completed_at") or s.get("started_at")),
        )
        if r or status == "weak" or s.get("outcome") == "needs_review" or stale:
            reason = (
                ("Review overdue" if r["overdue"] else "Review scheduled") if r
                else "Weak — under 40%" if status == "weak"
                else f"Not practised in {p.get('days_since')} days" if stale
                else "Last session said review"
            )
            needs_review.append({**base, "reason": reason, "due_at": r.get("due_at") if r else None, "overdue": bool(r and r["overdue"])})
        elif status == "mastered" or s.get("outcome") == "mastered":
            mastered.append(base)
        else:
            phase = s.get("current_phase") if not s.get("outcome") else None
            in_progress.append({
                **base,
                "phase": _PHASE_WORD.get(phase or ""),
                "study_session_id": s.get("study_session_id") if phase else None,
            })

    needs_review.sort(key=lambda r: (not r["overdue"], r.get("proficiency") or 0))
    in_progress.sort(key=lambda r: str(r.get("last_practised") or ""), reverse=True)

    # ── coverage: the JEE syllabus, unit by unit, in exam order ──
    done_ids = {r["concept_id"] for r in mastered}
    started_ids = touched
    review_ids = {r["concept_id"] for r in needs_review}
    topics = _syllabus_topics()
    if topics:
        syllabus, coverage = _topic_coverage(topics, done_ids, started_ids, review_ids, progress, latest_session)
    else:
        syllabus, coverage = _chapter_coverage(idx, done_ids, started_ids, progress, latest_session)

    # ── up next ──
    up_next: List[Dict[str, Any]] = []
    seen: set = set()

    def add(cid: str, kind: str, reason: str, **extra: Any) -> None:
        if cid in seen or len(up_next) >= 3:
            return
        seen.add(cid)
        up_next.append({**_row(cid, idx), "kind": kind, "reason": reason, **extra})

    for r in needs_review:
        if r["overdue"]:
            add(r["concept_id"], "review", f"Review due — {r['reason'].lower()}.")
    for r in in_progress:
        if r.get("study_session_id"):
            add(r["concept_id"], "resume", f"You stopped in {r['phase']}. Pick it up where you left off.",
                study_session_id=r["study_session_id"])
            break
    # Builds on something mastered: the strongest reason to go next.
    try:
        from retrieval import store

        for m in sorted(mastered, key=lambda r: str(r.get("last_practised") or ""), reverse=True):
            for dep in store.dependents(m["concept_id"]):
                if dep not in started_ids and dep in idx["concepts"]:
                    add(dep, "next", f"You mastered {m['name']} — {idx['concepts'][dep].get('name')} builds on it.")
                    break
    except Exception:
        pass
    for c in coverage:
        if c["next"]:
            nxt = c["next"][0]
            add(nxt["concept_id"], "next", f"Next in {c['chapter']} — {c['started']} of {c['total']} syllabus topics started.")
    for r in needs_review:
        add(r["concept_id"], "review", f"{r['reason']}.")
    if not up_next:
        # A new student starts at the first JEE syllabus topic, not at whatever
        # glossary term ("law") happens to come first in the textbook.
        start = next((t for t in topics if t["concept_id"] in idx["concepts"]), None) if topics else None
        if start:
            add(start["concept_id"], "start", f"Start of the JEE syllabus: {start['unit']} — {start['topic']}.")
        elif idx["chapters"]:
            first = idx["by_chapter"][idx["chapters"][0]["chapter_id"]][0]
            add(first["concept_id"], "start", f"Start at the beginning of the syllabus: {idx['chapters'][0]['title']}.")

    return {
        "generated_at": now,
        "up_next": up_next,
        "mastered": _group(mastered, idx),
        "in_progress": _group(in_progress, idx),
        "needs_review": _group(needs_review, idx),
        "counts": {"mastered": len(mastered), "in_progress": len(in_progress), "needs_review": len(needs_review)},
        "coverage": coverage,
        "syllabus": syllabus,
    }


def _syllabus_topics() -> List[Dict[str, Any]]:
    """The JEE syllabus topic table (local_ingest/syllabus_topics.py), exam order."""
    try:
        from retrieval import store

        return store.syllabus_topics()
    except Exception:
        return []  # an index built before the topic layer


def _topic_coverage(
    topics: List[Dict[str, Any]],
    done_ids: set,
    started_ids: set,
    review_ids: set,
    progress: Dict[str, Dict[str, Any]],
    latest_session: Dict[str, Dict[str, Any]],
) -> tuple:
    # A student studies glossary concepts ("law of inertia", "LC circuit") that
    # are not themselves syllabus topics; credit each to its nearest topic in the
    # same unit so the syllabus reflects everything they have done.
    owner = _nearest_topics(topics, started_ids)
    members: Dict[str, set] = {}
    for cid, topic_cid in owner.items():
        members.setdefault(topic_cid, set()).add(cid)

    def status(cid: str) -> str:
        group = members.get(cid, set()) | ({cid} if cid in started_ids else set())
        if not group:
            return "new"
        if group & review_ids:
            return "review"
        if group <= done_ids:
            return "mastered"
        return "started"

    units: Dict[str, Dict[str, Any]] = {}
    unit_last: Dict[str, str] = {}
    for t in topics:
        u = units.setdefault(t["unit"], {"unit": t["unit"], "total": 0, "mastered": 0, "started": 0, "topics": []})
        st = status(t["concept_id"])
        u["topics"].append({
            "topic": t["topic"],
            "concept_id": t["concept_id"],
            "status": st,
            # No textbook section behind it: the tutor teaches it from JEE physics.
            "tutor_taught": int(t.get("material") or 0) < 3,
        })
        u["total"] += 1
        u["mastered"] += st == "mastered"
        u["started"] += st != "new"
        if st != "new":
            for cid in members.get(t["concept_id"], set()) | {t["concept_id"]}:
                when = _iso((progress.get(cid) or {}).get("last_practised")
                            or (latest_session.get(cid) or {}).get("started_at")) or ""
                unit_last[t["unit"]] = max(unit_last.get(t["unit"], ""), when)
    syllabus = list(units.values())

    coverage = []
    for unit in sorted(unit_last, key=lambda u: unit_last[u], reverse=True)[:3]:
        u = units[unit]
        remaining = [t for t in u["topics"] if t["status"] == "new"]
        coverage.append({
            "chapter_id": unit,
            "chapter": unit,
            "total": u["total"],
            "mastered": u["mastered"],
            "started": u["started"],
            "next": [{"concept_id": t["concept_id"], "name": t["topic"]} for t in remaining[:5]],
            "remaining": len(remaining),
        })
    return syllabus, coverage


def _nearest_topics(topics: List[Dict[str, Any]], concept_ids: set) -> Dict[str, str]:
    """concept_id -> the topic concept it belongs to (same JEE unit, closest vector)."""
    topic_ids = {t["concept_id"] for t in topics}
    loose = [c for c in concept_ids if c not in topic_ids]
    if not loose:
        return {}
    try:
        import numpy as np
        from retrieval import store

        vecs = store.concept_vectors()
        rows = {r["concept_id"]: r for r in store.concepts_by_ids(list(topic_ids | set(loose)))}
    except Exception:
        return {}
    by_unit: Dict[str, List[str]] = {}
    for t in topics:
        by_unit.setdefault(t["unit"], []).append(t["concept_id"])
    out: Dict[str, str] = {}
    for cid in loose:
        row = rows.get(cid)
        if not row or row.get("vec_row") is None:
            continue
        candidates = [tc for tc in by_unit.get(row.get("unit") or "", []) if rows.get(tc, {}).get("vec_row") is not None]
        if not candidates:
            continue
        sims = vecs[[int(rows[tc]["vec_row"]) for tc in candidates]] @ vecs[int(row["vec_row"])]
        out[cid] = candidates[int(np.argmax(sims))]
    return out


def _chapter_coverage(idx, done_ids, started_ids, progress, latest_session) -> tuple:
    """Fallback for an index without the syllabus topic table: OpenStax chapters."""
    chapter_last: Dict[str, str] = {}
    for cid in started_ids:
        ch = (idx["concepts"].get(cid) or {}).get("chapter_id")
        when = _iso((progress.get(cid) or {}).get("last_practised") or (latest_session.get(cid) or {}).get("started_at")) or ""
        if ch and when > chapter_last.get(ch, ""):
            chapter_last[ch] = when
    units: Dict[str, Dict[str, Any]] = {}
    for ch in idx["chapters"]:
        rows = idx["by_chapter"].get(ch["chapter_id"], [])
        unit = units.setdefault(ch.get("jee_unit") or ch.get("unit"), {
            "unit": ch.get("jee_unit") or ch.get("unit"), "total": 0, "mastered": 0, "started": 0, "topics": []})
        for c in rows:
            st = "mastered" if c["concept_id"] in done_ids else "started" if c["concept_id"] in started_ids else "new"
            unit["topics"].append({"topic": c.get("name"), "concept_id": c["concept_id"], "status": st, "tutor_taught": False})
            unit["total"] += 1
            unit["mastered"] += st == "mastered"
            unit["started"] += st != "new"
    coverage = []
    for ch_id in sorted(chapter_last, key=lambda c: chapter_last[c], reverse=True)[:3]:
        rows = idx["by_chapter"].get(ch_id, [])
        remaining = [c for c in rows if c["concept_id"] not in started_ids]
        coverage.append({
            "chapter_id": ch_id, "chapter": rows[0].get("chapter") if rows else ch_id, "total": len(rows),
            "mastered": sum(1 for c in rows if c["concept_id"] in done_ids),
            "started": sum(1 for c in rows if c["concept_id"] in started_ids),
            "next": [{"concept_id": c["concept_id"], "name": c.get("name")} for c in remaining[:5]],
            "remaining": len(remaining),
        })
    return list(units.values()), coverage
