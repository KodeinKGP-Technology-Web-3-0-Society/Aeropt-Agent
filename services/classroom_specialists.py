"""Classroom Phase 3 specialists: plan, daily, revise, readiness.

Pure functions over the JSON Django sends. No chat, no stream, no LLM.
`load_learner` is the student-agent: it normalizes the body and is not a route.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


def load_learner(body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize the Django request body into a learner context dict."""
    raw = body if isinstance(body, dict) else {}
    home_raw = raw.get("home") if isinstance(raw.get("home"), dict) else {}

    readiness_raw = home_raw.get("readiness")
    if not isinstance(readiness_raw, dict):
        readiness_raw = {}
    score = readiness_raw.get("score", 0)
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0
    label = str(readiness_raw.get("label") or "early").strip().lower()
    if label not in ("early", "building", "ready"):
        label = "early"

    for_date = raw.get("for_date")
    for_date = str(for_date).strip() if for_date is not None else ""

    return {
        "for_date": for_date,
        "skip_concept_ids": _skip_ids(raw.get("skip_concept_ids")),
        "home": {
            "today": home_raw.get("today"),
            "plan": _list_of_dicts(home_raw.get("plan")),
            "map": _list_of_dicts(home_raw.get("map")),
            "revise": _list_of_dicts(home_raw.get("revise")),
            "readiness": {"score": score, "label": label},
        },
        "catalog": _list_of_dicts(raw.get("catalog")),
        "trackers": _list_of_dicts(raw.get("trackers")),
    }


def plan_specialist(learner: Dict[str, Any]) -> Dict[str, Any]:
    """Build the week plan using the LLM study planner agent."""
    from agents.study_planner import generate_study_plan
    from db.supabase_client import db
    
    catalog = learner["catalog"]
    if not catalog:
        return {"plan": list(learner.get("home", {}).get("plan", []))}

    user_id = learner.get("user_id")
    user_profile = {}
    if user_id:
        user_profile = db.read_user(user_id) or {}
        
    trackers = learner.get("trackers", [])
    
    plan = generate_study_plan(user_profile, trackers, catalog)
    mastery_by_id = _mastery_index(learner)
    catalog_ids = {_concept_id(row) for row in catalog if _concept_id(row)}

    if plan:
        filtered_plan = []
        for i, p in enumerate(plan):
            cid = p.get("concept_id")
            if cid in catalog_ids and mastery_by_id.get(cid, 0) < 80:
                item = dict(p)
                if not item.get("status"):
                    item["status"] = "in_progress" if not filtered_plan else "pending"
                if not item.get("when"):
                    item["when"] = "today" if not filtered_plan else "this_week"
                filtered_plan.append(item)
        plan = filtered_plan

    if not plan:
        # Fallback to static plan logic
        plan = []
        for row in catalog:
            concept_id = _concept_id(row)
            if not concept_id:
                continue
            if mastery_by_id.get(concept_id, 0) >= 80:
                continue
            title = _title_of(row) or _title_lookup(learner, concept_id)
            if not title:
                continue
            if not plan:
                status, when = "in_progress", "today"
            else:
                status, when = "pending", "this_week"
            plan.append(
                {
                    "concept_id": concept_id,
                    "title": title,
                    "status": status,
                    "when": when,
                }
            )
    return {"plan": plan}


def revise_specialist(learner: Dict[str, Any]) -> Dict[str, Any]:
    """Queue weak trackers, else keep home.revise. First due today."""
    weak: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for row in learner["trackers"]:
        if not bool(row.get("flagged_weak")):
            continue
        concept_id = _concept_id(row)
        if not concept_id or concept_id in seen:
            continue
        title = _title_of(row) or _title_lookup(learner, concept_id)
        if not title:
            continue
        seen.add(concept_id)
        weak.append({"concept_id": concept_id, "title": title})

    source = weak if weak else []
    if not source:
        for row in learner["home"]["revise"]:
            concept_id = _concept_id(row)
            if not concept_id or concept_id in seen:
                continue
            title = _title_of(row) or _title_lookup(learner, concept_id)
            if not title:
                continue
            seen.add(concept_id)
            source.append({"concept_id": concept_id, "title": title})

    revise: List[Dict[str, str]] = []
    for i, row in enumerate(source):
        revise.append(
            {
                "concept_id": row["concept_id"],
                "title": row["title"],
                "due": "today" if i == 0 else "this_week",
            }
        )
    return {"revise": revise}


def readiness_specialist(learner: Dict[str, Any]) -> Dict[str, Any]:
    """Mean mastery of attempted trackers → score and label."""
    scores: List[int] = []
    for row in learner["trackers"]:
        try:
            attempts = int(row.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts < 1:
            continue
        try:
            mastery = int(row.get("mastery") or 0)
        except (TypeError, ValueError):
            mastery = 0
        scores.append(mastery)

    if not scores:
        return {"readiness": {"score": 0, "label": "early"}}

    score = int(round(sum(scores) / len(scores)))
    if score < 40:
        label = "early"
    elif score < 75:
        label = "building"
    else:
        label = "ready"
    return {"readiness": {"score": score, "label": label}}


def daily_specialist(learner: Dict[str, Any]) -> Dict[str, Any]:
    """Pick today's block: revise → plan lesson → assess (building only)."""
    for_date = learner["for_date"]
    home = learner["home"]
    skipped = set(learner.get("skip_concept_ids") or [])

    # Same queue the revision specialist would store, so a weak tracker
    # is today's block even when home.revise is still the previous list.
    revise_rows = [
        row for row in revise_specialist(learner)["revise"] if _concept_id(row) not in skipped
    ]
    due_today = [r for r in revise_rows if str(r.get("due") or "").strip() == "today"]
    revise_pick = due_today[0] if due_today else (revise_rows[0] if revise_rows else None)
    if revise_pick is not None:
        today = _today_block(learner, revise_pick, action="revise", for_date=for_date)
        if today is not None:
            return {"today": today}

    plan_rows = [
        row
        for row in (home["plan"] or plan_specialist(learner)["plan"])
        if _concept_id(row) not in skipped
    ]
    plan_today = [r for r in plan_rows if str(r.get("when") or "").strip() == "today"]
    if plan_today:
        plan_pick = plan_today[0]
    else:
        plan_pick = None
        for row in plan_rows:
            status = str(row.get("status") or "").strip()
            if status in ("in_progress", "pending"):
                plan_pick = row
                break
    if plan_pick is not None:
        today = _today_block(learner, plan_pick, action="lesson", for_date=for_date)
        if today is not None:
            return {"today": today}

    label = home["readiness"]["label"]
    if any(_attempts(row) >= 1 for row in learner["trackers"]):
        label = readiness_specialist(learner)["readiness"]["label"]
    if label == "building":
        for row in list(home["map"]) + list(learner["trackers"]):
            if _concept_id(row) in skipped:
                continue
            try:
                mastery = int(row.get("mastery") or 0)
            except (TypeError, ValueError):
                mastery = 0
            if mastery < 50:
                continue
            today = _today_block(learner, row, action="assess", for_date=for_date)
            if today is not None:
                return {"today": today}

    return {"today": None}


def _today_block(
    learner: Dict[str, Any],
    row: Dict[str, Any],
    *,
    action: str,
    for_date: str,
) -> Optional[Dict[str, Any]]:
    concept_id = _concept_id(row)
    if not concept_id:
        return None
    title = _title_of(row) or _title_lookup(learner, concept_id)
    if not title:
        return None
    return {
        "concept_id": concept_id,
        "title": title,
        "action": action,
        "minutes": 25,
        "for_date": for_date,
    }


def _skip_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    skip: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in skip:
            skip.append(text)
    return skip


def _list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _attempts(row: Dict[str, Any]) -> int:
    try:
        return int(row.get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


def _concept_id(row: Dict[str, Any]) -> str:
    return str(row.get("concept_id") or "").strip()


def _title_of(row: Dict[str, Any]) -> str:
    return str(row.get("title") or "").strip()


def _mastery_index(learner: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in list(learner["home"]["map"]) + list(learner["trackers"]):
        concept_id = _concept_id(row)
        if not concept_id:
            continue
        try:
            mastery = int(row.get("mastery") or 0)
        except (TypeError, ValueError):
            mastery = 0
        # Highest wins when both map and tracker mention the concept.
        if concept_id not in out or mastery > out[concept_id]:
            out[concept_id] = mastery
    return out


def _title_lookup(learner: Dict[str, Any], concept_id: str) -> str:
    home = learner["home"]
    for row in (
        list(learner["catalog"])
        + list(learner["trackers"])
        + list(home["map"])
        + list(home["plan"])
        + list(home["revise"])
    ):
        if _concept_id(row) == concept_id:
            title = _title_of(row)
            if title:
                return title
    return ""
