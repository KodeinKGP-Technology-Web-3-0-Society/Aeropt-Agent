"""Classroom rooms share the Main Tutor.

A surface is an input shape. It writes a job card onto the turn. It does not
answer by itself, and it does not guess the job from the student's words.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.classroom_evidence import post_events

_SURFACES = {"lesson", "doubt", "assess"}
_BUDGET = {"lesson": 6, "doubt": 6, "assess": 4}


def prerequisite_ids(concept_id: str) -> List[str]:
    """Prerequisite concept ids for a locked room. Empty when the graph is down."""
    concept_id = (concept_id or "").strip()
    if not concept_id:
        return []
    try:
        from retrieval.local_graph import graph_client

        rows = graph_client().get_prerequisite_concepts(concept_id, max_depth=2) or []
    except Exception:
        return []
    ids: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("concept_id") or "").strip()
        if cid and cid != concept_id and cid not in ids:
            ids.append(cid)
    return ids


def job_card(
    surface: str,
    concept_id: str,
    prereqs: List[str],
    *,
    assess_action: str = "build",
    work_check: Optional[Dict[str, Any]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    grade: Optional[Dict[str, Any]] = None,
) -> str:
    """Instructions for this room. The tutor still writes the only reply."""
    concept = concept_id or "the concept already in this session"
    prereq_text = ", ".join(prereqs) if prereqs else "none on record"
    lines = [
        f"Classroom job: {surface}.",
        f"Stay on {concept}. Prerequisites you may use: {prereq_text}.",
        "You are the only voice the student hears. A helper's findings stay inside your reply.",
    ]
    if surface == "lesson":
        lines.append("Teach this concept. Do not switch to a different topic.")
    elif surface == "doubt":
        lines.append(
            "The student is stuck. If their working is included below, use that check. "
            "When they need a full solution, call consult_expert with worked_solution. "
            "Do not open a second conversation."
        )
        if work_check:
            lines.append(f"Working check: {work_check}")
    elif surface == "assess" and assess_action == "grade":
        lines.append(
            "Explain the grade below. The correct or incorrect marks came from the "
            "answer key and the same proficiency maths as a practice button. "
            "Do not treat a mark the student sent as the grade."
        )
        if grade:
            lines.append(f"Grade: {grade}")
    else:
        lines.append(
            "This is an assessment. Present only the retrieved items below. "
            "Do not invent questions, and do not grade yet."
        )
        if items:
            lines.append(f"Retrieved items: {items}")
    return "\n".join(lines)


def prepare_room(
    *,
    surface: Optional[str],
    concept_id: Optional[str],
    student_work: Optional[str] = None,
    assess_action: Optional[str] = None,
    submissions: Optional[List[Dict[str, Any]]] = None,
    user_id: str = "",
    session_id: str = "",
    token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """State to merge before the tutor graph. None when this is not a room."""
    room = (surface or "").strip().lower()
    if room not in _SURFACES:
        return None
    concept = (concept_id or "").strip()
    prereqs = prerequisite_ids(concept) if concept else []
    action = (assess_action or "").strip().lower()
    if room == "assess" and action not in {"build", "grade"}:
        action = "grade" if submissions else "build"

    work_check = None
    if room == "doubt" and (student_work or "").strip():
        work_check = check_handed_work(student_work or "")

    items = None
    grade = None
    if room == "assess" and action == "grade":
        grade = grade_submissions(
            concept,
            submissions or [],
            user_id=user_id,
            session_id=session_id,
            token=token,
        )
    elif room == "assess":
        items = build_assessment(concept).get("items") or []

    job = job_card(
        room,
        concept,
        prereqs,
        assess_action=action or "build",
        work_check=work_check,
        items=items,
        grade=grade,
    )
    overrides: Dict[str, Any] = {
        "classroom_surface": room,
        "classroom_job": job,
        "classroom_max_steps": _BUDGET[room],
    }
    if concept:
        overrides["locked_concept_id"] = concept
        overrides["primary_concept_id"] = concept
        overrides["secondary_concept_ids"] = prereqs
    if room == "assess" and action != "grade":
        overrides["content_type_requested"] = "practice"
        overrides["study_stage"] = "practice"
    return {"state_overrides": overrides, "grade": grade, "work_check": work_check}


def check_handed_work(student_work: str) -> Dict[str, Any]:
    """Run the physics work check before the tutor speaks. Not a keyword route."""
    steps = [line.strip() for line in (student_work or "").splitlines() if line.strip()]
    if len(steps) < 2:
        steps = [part.strip() for part in re.split(r"(?<=[.;])\s+", student_work or "") if part.strip()]
    if len(steps) < 2:
        return {"error": "Need at least two steps of working to check."}
    from mcp_tools.physics_tools import CheckStudentWorkArgs, check_student_work

    return check_student_work(CheckStudentWorkArgs(steps=steps), None)  # type: ignore[arg-type]


def build_assessment(concept_id: str) -> Dict[str, Any]:
    """Practice items from retrieval for this concept. No invented prompts."""
    concept = (concept_id or "").strip()
    if not concept:
        return {"items": []}
    from agents.graphrag_retriever import graphrag_retriever_node

    retrieved = graphrag_retriever_node(
        {
            "query": concept,
            "user_id": "",
            "session_id": "",
            "primary_concept_id": concept,
            "locked_concept_id": concept,
            "content_type_requested": "practice",
            "target_exam": "JEE_MAINS",
        }
    )
    items: List[Dict[str, str]] = []
    for chunk in retrieved.get("retrieved_chunks") or []:
        if not isinstance(chunk, dict):
            continue
        item_id = str(chunk.get("content_id") or "").strip()
        prompt = str(chunk.get("core_text") or chunk.get("heading") or "").strip()
        if not item_id or not prompt:
            continue
        items.append({"item_id": item_id, "prompt": prompt[:800]})
        if len(items) == 4:
            break
    return {"items": items}


def grade_submissions(
    concept_id: str,
    submissions: List[Dict[str, Any]],
    *,
    user_id: str = "",
    session_id: str = "",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Grade against the stored solution. A client `correct` flag is ignored."""
    concept = (concept_id or "").strip()
    keys = _solution_index([str(row.get("item_id") or "") for row in submissions if isinstance(row, dict)])
    correct_count = 0
    graded = 0
    ungraded = 0
    events: List[Dict[str, Any]] = []
    last_mastery: Optional[int] = None
    details: List[Dict[str, Any]] = []

    for row in submissions:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id") or "").strip()
        answer = str(row.get("answer") or "").strip()
        key = keys.get(item_id) or ""
        if not item_id or not answer or not key:
            ungraded += 1
            details.append({"item_id": item_id, "graded": False})
            continue
        is_correct = _normalize(answer) == _normalize(key)
        graded += 1
        if is_correct:
            correct_count += 1
        mastery = _record_diagnostic(
            user_id=user_id,
            session_id=session_id,
            concept_id=concept,
            answer=answer,
            is_correct=is_correct,
        )
        if mastery is not None:
            last_mastery = mastery
        events.append(
            {
                "concept_id": concept,
                "surface": "assess",
                "specialist": "evaluation",
                "kind": "attempt",
                "payload": {"correct": is_correct, **({"mastery": mastery} if mastery is not None else {})},
            }
        )
        details.append({"item_id": item_id, "graded": True, "correct": is_correct})

    if graded and last_mastery is None:
        last_mastery = round(100 * correct_count / graded)
    if graded and concept and last_mastery is not None:
        events.append(
            {
                "concept_id": concept,
                "surface": "assess",
                "specialist": "evaluation",
                "kind": "mastery",
                "payload": {"mastery": last_mastery},
            }
        )
    if events:
        post_events(events, token=token)
    return {
        "mastery": last_mastery if graded else None,
        "correct": correct_count,
        "attempts": graded,
        "ungraded": ungraded,
        "items": details,
    }


def handoff_events(concept_id: str, trace_path: Optional[List[str]]) -> List[Dict[str, Any]]:
    """One bus handoff when the tutor actually called the worked-solution expert."""
    path = trace_path or []
    if not any("worked_solution" in step for step in path):
        return []
    concept = (concept_id or "").strip()
    return [
        {
            "concept_id": concept,
            "surface": "doubt",
            "specialist": "tutor",
            "kind": "handoff",
            "payload": {"target": "solver", "status": "done"},
        }
    ]


def _solution_index(item_ids: List[str]) -> Dict[str, str]:
    ids = [item for item in item_ids if item]
    if not ids:
        return {}
    try:
        from retrieval import store

        if not store.index_exists():
            return {}
        rows = store.chunks_by_ids(ids)
    except Exception:
        return {}
    found: Dict[str, str] = {}
    for row in rows:
        content_id = str(row.get("content_id") or "")
        solution = str(row.get("solution") or "").strip()
        if content_id and solution:
            found[content_id] = solution
    return found


def _record_diagnostic(
    *,
    user_id: str,
    session_id: str,
    concept_id: str,
    answer: str,
    is_correct: bool,
) -> Optional[int]:
    if not user_id or not concept_id:
        return None
    from agents.diagnostic import diagnostic_node

    try:
        result = diagnostic_node(
            {
                "user_id": user_id,
                "session_id": session_id,
                "primary_concept_id": concept_id,
                "is_correct": is_correct,
                "student_answer": answer,
                "query": answer,
            }
        )
    except Exception:
        return None
    mastery = result.get("mastery_pct")
    if isinstance(mastery, int) and not isinstance(mastery, bool) and 0 <= mastery <= 100:
        return mastery
    return None


def _normalize(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text.strip().lower())
    return collapsed.replace("$", "").replace(",", "")
