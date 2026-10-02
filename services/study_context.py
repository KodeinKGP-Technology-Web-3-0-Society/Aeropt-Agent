"""What the Study Room knows about a student before the teacher says a word.

Deterministic — no model runs here. The teacher used to be told to call
`get_learner_context` / `get_study_context` itself; in practice it rarely did,
and each call cost a reasoning step. So the snapshot is assembled up front and
put in the system prompt, and the same dict is returned to the UI so the
student can see what the tutor remembers.

What goes in, for the concept being studied:
  * past study sessions on it (outcome, score, difficulty reached)
  * its prerequisites, each with the student's proficiency and any study
    session on it — so "you finished self-inductance yesterday" is known
  * other recent study sessions in the same chapter
  * recent wrong answers on this concept and its prerequisites
  * durable learner memories (misconceptions, preferences, strengths)
"""

from __future__ import annotations

import json
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.supabase_client import db

logger = logging.getLogger("jee_tutor.study_context")

_FINISHED = ("mastered", "needs_review")


# ── concept resolution ────────────────────────────────────────────────────────


def _store():
    try:
        from retrieval import store

        return store if store.index_exists() else None
    except Exception:
        return None


def concept_details(concept_id: str) -> Optional[Dict[str, Any]]:
    """Name/chapter/definition for a concept id from the local index."""
    st = _store()
    if not st or not concept_id:
        return None
    try:
        row = st.concept(concept_id)
    except Exception:
        return None
    if not row:
        return None
    try:
        linked = st.corpus().execute(
            "SELECT COUNT(*) FROM chunk_concepts WHERE concept_id = ?", (concept_id,)
        ).fetchone()[0]
    except Exception:
        linked = 0
    return {
        "concept_id": row["concept_id"],
        "concept_name": row.get("name") or concept_id,
        "chapter": row.get("chapter"),
        "unit": row.get("unit"),
        # Syllabus topics carry a placeholder definition; show none rather than that.
        "definition": None if row.get("source") == "syllabus" else row.get("definition"),
        "in_syllabus": bool(row.get("in_syllabus", 1)),
        # Syllabus topics OpenStax never teaches (lab experiments, logic gates):
        # the tutor teaches them without a textbook section behind it.
        "has_material": linked >= 3,
    }


def _topic_match(text: str) -> Optional[Dict[str, Any]]:
    """Exact match on a JEE syllabus topic name ("Kirchhoff's laws", "Logic gates")."""
    st = _store()
    if not st:
        return None

    def key(s: str) -> str:
        s = re.sub(r"\s*\([^)]*\)\s*$", "", s or "")
        return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("’", "'")).strip()

    needle = key(text)
    if not needle:
        return None
    try:
        rows = st.corpus().execute("SELECT topic, concept_id FROM syllabus_topics").fetchall()
    except Exception:
        return None  # index built before the topic layer
    for row in rows:
        if key(row["topic"]) == needle:
            return concept_details(row["concept_id"])
    # Then the phrases each topic lists ("Gauss's law" for "Gauss's law and its
    # applications"), in syllabus order so the first-listed topic wins.
    try:
        from local_ingest.jee_topics import JEE_TOPICS
    except Exception:
        return None
    by_topic = {key(r["topic"]): r["concept_id"] for r in rows}
    for _unit, topics in JEE_TOPICS:
        for name, *phrases in topics:
            if any(key(p) == needle for p in phrases) and key(name) in by_topic:
                return concept_details(by_topic[key(name)])
    return None


def _name_match(text: str) -> Optional[Dict[str, Any]]:
    """Exact (case-insensitive) match on a concept's name or one of its aliases."""
    st = _store()
    if not st:
        return None
    needle = " ".join(text.lower().replace("_", " ").split())
    if not needle:
        return None
    try:
        for row in st.all_concepts():
            names = [row.get("name") or "", row["concept_id"].replace("_", " ")]
            try:
                names += json.loads(row.get("aliases") or "[]")
            except (TypeError, ValueError):
                pass
            if any(" ".join(n.lower().split()) == needle for n in names if n):
                return concept_details(row["concept_id"])
    except Exception:
        logger.debug("name match failed", exc_info=True)
    return None


# Abbreviations students type that the resolver cannot expand on its own.
_ABBREVIATIONS = {
    "shm": "simple_harmonic_motion_shm",
    "com": "center_of_mass",
    "moi": "moment_of_inertia",
    "ke": "kinetic_energy",
    "pe": "potential_energy",
    "rms": "rms_current",
}


def resolve_study_concept(text: str) -> Dict[str, Any]:
    """Turn what the student typed ("LC oscillation", "torque") into a concept.

    Returns concept_id/concept_name/chapter plus up to four `alternatives` so the
    UI can offer "Not this? Try …" when the resolver guessed wrong. An exact id
    or name wins outright; otherwise the retrieval resolver ranks candidates.
    """
    text = (text or "").strip()
    abbreviated = _ABBREVIATIONS.get(text.lower().strip(". "))
    exact = (
        (abbreviated and concept_details(abbreviated))
        or concept_details(text)
        or _topic_match(text)
        or _name_match(text)
    )
    if exact:
        return {**exact, "resolved_from": text, "alternatives": [], "how": "exact"}

    try:
        from retrieval import resolver

        res = resolver.resolve(text)
    except Exception:
        logger.debug("resolver failed for %r", text, exc_info=True)
        res = None

    if res is None or not res.concept_id:
        return {
            "concept_id": text,
            "concept_name": text,
            "chapter": None,
            "resolved_from": text,
            "alternatives": [],
            "how": "unresolved",
            "in_syllabus": False,
        }

    # The resolver's top pick can lose to a candidate whose name literally
    # contains what was typed ("LC oscillation" → "LC circuit", not the quantum
    # oscillator), so prefer a candidate sharing the typed acronym/word.
    candidates = list(res.candidates or [])
    # Acronyms only (LC, RLC, SHM, AC) — the typed form, uppercase. A looser
    # "shared short word" rule matched "law" and sent "Gauss's law" to the law
    # of inertia.
    acronyms = {w.lower() for w in re.findall(r"\b[A-Z]{2,4}\b", text)}
    best = res.concept_id
    if acronyms:
        top_words = set((res.concept_name or "").lower().replace("-", " ").split())
        if not acronyms & top_words:
            for cand in candidates:
                name_words = set((cand.get("name") or "").lower().replace("-", " ").split())
                if acronyms & name_words:
                    best = cand["concept_id"]
                    break

    picked = concept_details(best) or {
        "concept_id": best,
        "concept_name": res.concept_name or best,
        "chapter": res.chapter,
    }
    alternatives = [
        {"concept_id": c["concept_id"], "concept_name": c.get("name") or c["concept_id"], "chapter": c.get("chapter")}
        for c in candidates
        if c.get("concept_id") and c["concept_id"] != picked["concept_id"]
    ][:4]
    return {
        **picked,
        "resolved_from": text,
        "alternatives": alternatives,
        "how": "resolver",
        "in_syllabus": bool(res.in_syllabus) if res.in_scope else False,
    }


# ── learner snapshot ──────────────────────────────────────────────────────────


def _days_ago(ts: Any) -> Optional[float]:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 86400)
    except (TypeError, ValueError):
        return None


def _when(ts: Any) -> str:
    d = _days_ago(ts)
    if d is None:
        return "earlier"
    if d < 1 / 24:
        return "just now"
    if d < 1:
        return f"{int(d * 24)}h ago"
    if d < 2:
        return "yesterday"
    return f"{int(d)} days ago"


def _session_view(s: Dict[str, Any]) -> Dict[str, Any]:
    served = int(s.get("questions_served") or 0)
    correct = int(s.get("questions_correct") or 0)
    return {
        "study_session_id": s.get("study_session_id"),
        "concept_id": s.get("concept_id"),
        "concept_name": s.get("concept_name") or s.get("concept_id"),
        "chapter": s.get("chapter"),
        "outcome": s.get("outcome"),
        "phase": s.get("current_phase"),
        "questions_served": served,
        "questions_correct": correct,
        "difficulty_reached": s.get("difficulty_level"),
        "when": _when(s.get("completed_at") or s.get("started_at")),
        "started_at": s.get("started_at"),
    }


def _proficiency(user_id: str, concept_id: str) -> Optional[Dict[str, Any]]:
    try:
        cs = db.read_user_concept_state(user_id, concept_id)
    except Exception:
        return None
    if not cs or not int(cs.get("attempts") or 0):
        return None  # cold start carries no information
    return {
        "proficiency": round(float(cs.get("proficiency_score") or 0), 2),
        "attempts": int(cs.get("attempts") or 0),
        "correct": int(cs.get("correct") or 0),
        "flagged_weak": bool(cs.get("flagged_weak")),
    }


def _recent_mistakes(user_id: str, concept_ids: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    if not concept_ids:
        return []
    try:
        res = (
            db.client.table("user_attempts")
            .select("concept_id, is_correct, error_type, student_answer, created_at")
            .eq("user_id", user_id)
            .in_("concept_id", concept_ids)
            .eq("is_correct", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = res.data or []
    except Exception:
        return []
    return [
        {
            "concept_id": r.get("concept_id"),
            "error_type": r.get("error_type"),
            "student_answer": (r.get("student_answer") or "")[:120],
            "when": _when(r.get("created_at")),
        }
        for r in rows
    ]


def build_learner_snapshot(
    user_id: str,
    concept_id: str,
    *,
    exclude_study_session_id: Optional[str] = None,
    concept_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything the teacher should know about this student for this concept."""
    details = concept_details(concept_id) or {}
    name = concept_name or details.get("concept_name") or concept_id
    chapter = details.get("chapter")
    snap: Dict[str, Any] = {
        "concept_id": concept_id,
        "concept_name": name,
        "chapter": chapter,
        "has_material": details.get("has_material", True),
    }

    try:
        all_sessions = db.list_user_study_sessions(user_id, limit=40)
    except Exception:
        all_sessions = []
    # A session opened and abandoned in the first phase says nothing about the
    # student — the room used to create two of those per visit.
    all_sessions = [
        s for s in all_sessions
        if s.get("study_session_id") != exclude_study_session_id
        and (
            s.get("outcome") in _FINISHED
            or s.get("current_phase") not in (None, "learn")
            or int(s.get("questions_served") or 0) > 0
        )
    ]

    # 1. This concept, before today.
    same = [s for s in all_sessions if s.get("concept_id") == concept_id]
    finished_same = [s for s in same if s.get("outcome") in _FINISHED]
    snap["prior_sessions"] = [_session_view(s) for s in (finished_same or same)[:3]]
    if finished_same:
        best = max(int(s.get("difficulty_level") or 1) for s in finished_same)
        snap["recommended_difficulty"] = max(1, best - (0 if finished_same[0].get("outcome") == "mastered" else 1))

    own = _proficiency(user_id, concept_id)
    if own:
        snap["proficiency"] = own

    readiness = None
    try:
        readiness = db.read_concept_readiness(user_id, concept_id)
    except Exception:
        pass
    if readiness:
        snap["readiness"] = round(float(readiness.get("score") or 0), 1)

    # 2. Prerequisites — what this concept builds on, and how they went.
    prereqs: List[Dict[str, Any]] = []
    st = _store()
    if st:
        try:
            prereqs = st.prerequisites(concept_id, max_depth=2)[:8]
        except Exception:
            prereqs = []
    by_concept = {}
    for s in all_sessions:
        by_concept.setdefault(s.get("concept_id"), s)  # newest first
    prereq_rows = []
    for p in prereqs:
        cid = p["concept_id"]
        row: Dict[str, Any] = {"concept_id": cid, "concept_name": p.get("concept_name") or cid}
        prof = _proficiency(user_id, cid)
        if prof:
            row["proficiency"] = prof["proficiency"]
            row["attempts"] = prof["attempts"]
        studied = by_concept.get(cid)
        if studied:
            row["studied"] = _session_view(studied)
        prereq_rows.append(row)
    snap["prerequisites"] = prereq_rows

    # 3. Other recent work in the same chapter (the "related concept" case).
    prereq_ids = {p["concept_id"] for p in prereqs}
    related = [
        _session_view(s)
        for s in all_sessions
        if s.get("concept_id") not in prereq_ids
        and s.get("concept_id") != concept_id
        and chapter
        and (s.get("chapter") or (concept_details(s.get("concept_id") or "") or {}).get("chapter")) == chapter
    ]
    snap["related_sessions"] = related[:4]

    # 4. Where they went wrong recently, on this or what it rests on.
    snap["recent_mistakes"] = _recent_mistakes(user_id, [concept_id, *prereq_ids])

    # 5. Durable notes the tutors have written about them.
    try:
        memories = db.read_learner_memories(user_id, query=name, limit=8) or []
    except Exception:
        memories = []
    snap["memories"] = [
        {"kind": m.get("kind"), "fact": m.get("fact"), "about": m.get("subject_key")}
        for m in memories
        if m.get("fact")
    ]

    snap["returning"] = bool(snap["prior_sessions"])
    return snap


def format_snapshot(snap: Dict[str, Any]) -> str:
    """The snapshot as prompt text — facts only, one line each."""
    lines: List[str] = []
    if snap.get("has_material") is False:
        lines.append(
            f"- {snap['concept_name']} is a JEE syllabus topic with NO textbook section in the course "
            "material. retrieve_content will find little; teach it from standard JEE (NCERT-level) "
            "physics, verify every number with tools, and tell the student once that this topic is "
            "taught without a textbook section. There is no question bank for it: pose your own "
            "check questions, and say so if practice is thin."
        )

    prior = snap.get("prior_sessions") or []
    if prior:
        for s in prior:
            score = (
                f"{s['questions_correct']}/{s['questions_served']} correct"
                if s["questions_served"] else "no practice answered"
            )
            lines.append(
                f"- Studied {snap['concept_name']} before ({s['when']}): outcome "
                f"{s.get('outcome') or 'unfinished, stopped in ' + str(s.get('phase'))}, {score}, "
                f"difficulty reached {s.get('difficulty_reached')}/5."
            )
    else:
        lines.append(f"- First time studying {snap['concept_name']} in the Study Room.")

    prof = snap.get("proficiency")
    if prof:
        lines.append(
            f"- Proficiency on this concept: {prof['proficiency']} "
            f"({prof['correct']}/{prof['attempts']} attempts correct"
            f"{', flagged weak' if prof.get('flagged_weak') else ''})."
        )
    if snap.get("readiness") is not None:
        lines.append(f"- Exam readiness on this concept: {snap['readiness']}/100.")

    pre = snap.get("prerequisites") or []
    if pre:
        done, weak, unknown = [], [], []
        for p in pre:
            studied = p.get("studied")
            if studied and studied.get("outcome") == "mastered":
                done.append(f"{p['concept_name']} (mastered {studied['when']})")
            elif p.get("proficiency") is not None and p["proficiency"] >= 0.7:
                done.append(f"{p['concept_name']} (proficiency {p['proficiency']})")
            elif studied or p.get("proficiency") is not None:
                note = (
                    f"studied {studied['when']}, {studied['questions_correct']}/{studied['questions_served']}"
                    if studied else f"proficiency {p['proficiency']}"
                )
                weak.append(f"{p['concept_name']} ({note})")
            else:
                unknown.append(p["concept_name"])
        if done:
            lines.append("- Prerequisites already solid: " + "; ".join(done) + ". Build on these, don't re-teach them.")
        if weak:
            lines.append("- Prerequisites that are shaky: " + "; ".join(weak) + ". Check these quickly before relying on them.")
        if unknown:
            lines.append("- Prerequisites with no record yet: " + ", ".join(unknown) + ".")

    for s in snap.get("related_sessions") or []:
        score = f"{s['questions_correct']}/{s['questions_served']}" if s["questions_served"] else "no practice"
        lines.append(
            f"- Also worked on {s['concept_name']} in this chapter ({s['when']}, "
            f"{s.get('outcome') or 'unfinished'}, {score}). Connect to it where it helps."
        )

    for m in snap.get("recent_mistakes") or []:
        lines.append(
            f"- Recent wrong answer on {m['concept_id']} ({m['when']}): "
            f"{m.get('error_type') or 'unclassified'} error"
            + (f", answered “{m['student_answer']}”" if m.get("student_answer") else "")
            + "."
        )

    for m in snap.get("memories") or []:
        lines.append(f"- Note ({m.get('kind')}): {m.get('fact')}")

    return "\n".join(lines)


def public_snapshot(snap: Dict[str, Any]) -> Dict[str, Any]:
    """The subset the UI shows on the welcome card."""
    return {
        "returning": snap.get("returning", False),
        "prior_sessions": snap.get("prior_sessions") or [],
        "proficiency": snap.get("proficiency"),
        "readiness": snap.get("readiness"),
        "prerequisites": snap.get("prerequisites") or [],
        "related_sessions": snap.get("related_sessions") or [],
        "memories": [m for m in (snap.get("memories") or []) if m.get("kind") != "context"][:4],
        "recommended_difficulty": snap.get("recommended_difficulty"),
    }


# ── session runtime, rebuilt from the event log ───────────────────────────────


def session_runtime(study_session_id: str) -> Dict[str, Any]:
    """Open question, streaks and served ids for a study session.

    Derived from study_session_events rather than the per-process carry file,
    so a session resumed tomorrow (or on another worker) still has the question
    the student was answering. Streak rules mirror grade_practice_answer.
    """
    out: Dict[str, Any] = {
        "current_question": None,
        "awaiting_answer": False,
        "practice_streak": 0,
        "wrong_streak": 0,
        "served_content_ids": [],
        "last_answered": None,
    }
    try:
        events = db.read_study_events(study_session_id)
    except Exception:
        return out
    open_id: Optional[str] = None
    served: List[str] = []
    for e in events:
        et = e.get("event_type")
        pay = e.get("payload") or {}
        if et == "question_served" and e.get("content_id"):
            open_id = e["content_id"]
            served.append(open_id)
        elif et == "question_attempted":
            out["last_answered"] = {
                "content_id": e.get("content_id"),
                "is_correct": bool(pay.get("is_correct")),
                "student_answer": pay.get("student_answer"),
            }
            open_id = None
            if pay.get("is_correct"):
                out["practice_streak"] += 1
                out["wrong_streak"] = 0
            else:
                out["practice_streak"] = 0
                out["wrong_streak"] += 1
        elif et == "difficulty_adjusted":
            out["practice_streak"] = 0
            out["wrong_streak"] = 0
    out["served_content_ids"] = served
    last = out.get("last_answered")
    if last and last.get("content_id") and not open_id:
        # The question just answered, so the tutor can explain it on request.
        st = _store()
        try:
            rows = st.chunks_by_ids([last["content_id"]]) if st else []
        except Exception:
            rows = []
        if rows:
            last["question"] = rows[0]
    if open_id:
        st = _store()
        rows = []
        if st:
            try:
                rows = st.chunks_by_ids([open_id])
            except Exception:
                rows = []
        if rows:
            out["current_question"] = rows[0]
            out["awaiting_answer"] = True
    return out


def practice_question_view(q: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What the practice pane may show — never the solution."""
    if not q:
        return None
    try:
        difficulty = int(float(q.get("difficulty") or 1))
    except (TypeError, ValueError):
        difficulty = 1
    return {
        "content_id": q.get("content_id"),
        "question_text": q.get("core_text") or "",
        "question_type": q.get("question_type"),
        "difficulty": max(1, min(5, difficulty)),
        "has_solution": bool(q.get("solution")),
        "source": q.get("citation") or q.get("source_book"),
    }
