"""
Placement diagnostic for onboarding (Phase 1).

Serves practice questions from Supabase and grades via Diagnostic agent logic.
Probe selection is driven by Django placement planner (not random shuffle).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from db.supabase_client import db
from agents.diagnostic import diagnostic_node
from models.state import TutorState
from agents.solver import _image_urls, prepare_student_markdown


def _pick_concepts(question_count: int) -> List[Dict[str, Any]]:
    """Fallback when no probe plan is supplied (dev/offline)."""
    concepts = db.read_concepts()
    if not concepts:
        return []
    random.shuffle(concepts)
    return concepts[: max(1, min(question_count, len(concepts)))]


def _practice_for_concept(concept_id: str) -> Optional[Dict[str, Any]]:
    # The corpus lives in the retrieval database. This used to read `content` from the user
    # database (empty) and filter on content_type 'practice' (no such value), so placement
    # never found a question. Solved, figure-free questions first; any question otherwise.
    from retrieval import store

    rows = store.query(
        f"""select {store.CHUNK_COLUMNS} from public.content c
            where c.primary_concept_id = %s and c.is_question and coalesce(c.in_syllabus, true)
              and {store.FIGURE_READY_SQL}
            order by c.has_solution desc, (c.content_type = 'Practice Question') desc, c.content_id
            limit 5""",
        (concept_id,),
    )
    if not rows:
        return None
    return random.choice(rows)


def _ensure_concept_states(user_id: str) -> None:
    existing = db.read_all_user_concept_states(user_id)
    if existing:
        return
    concepts = db.read_concepts()
    if concepts:
        db.batch_create_user_concept_states(user_id, concepts)


def _concept_lookup() -> Dict[str, Dict[str, Any]]:
    return {row["concept_id"]: row for row in db.read_concepts()}


def _build_question(
    concept_id: str,
    chapter: str = "",
    concept_name: str = "",
) -> Optional[Dict[str, Any]]:
    lookup = _concept_lookup()
    concept = lookup.get(concept_id, {})
    chunk = _practice_for_concept(concept_id)
    if not chunk:
        return None
    text = (chunk.get("core_text") or "").strip()
    if not text:
        return None
    urls = _image_urls(chunk)
    return {
        "question_id": chunk["content_id"],
        "concept_id": concept_id,
        "concept_name": concept_name or concept.get("concept_name", concept_id),
        "chapter": chapter or concept.get("chapter") or chunk.get("chapter") or "",
        "question_text": text[:1200],
        "content_id": chunk["content_id"],
        "image_url": urls.get("core"),
        "solution_text": prepare_student_markdown((chunk.get("solution") or "").strip()),
        "solution_image_url": urls.get("solution"),
    }


def start_placement_diagnostic(
    user_id: str,
    question_count: int = 5,
    probes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    _ensure_concept_states(user_id)
    questions: List[Dict[str, Any]] = []

    if probes:
        for probe in probes:
            question = _build_question(
                concept_id=probe.get("concept_id", ""),
                chapter=probe.get("chapter", ""),
                concept_name=probe.get("concept_name", ""),
            )
            if question:
                questions.append(question)
    else:
        concepts = _pick_concepts(question_count)
        for concept in concepts:
            question = _build_question(
                concept_id=concept["concept_id"],
                chapter=concept.get("chapter") or "",
                concept_name=concept.get("concept_name", concept["concept_id"]),
            )
            if question:
                questions.append(question)

    return {"questions": questions}


def grade_placement_answer(
    user_id: str,
    concept_id: str,
    content_id: str,
    is_correct: bool,
) -> Dict[str, Any]:
    _ensure_concept_states(user_id)
    chunk = None
    if content_id:
        rows = db.read_content_by_ids([content_id])
        chunk = rows[0] if rows else None

    session = db.create_session(user_id)
    session_id = session["session_id"]

    state: TutorState = {
        "user_id": user_id,
        "session_id": session_id,
        "query": "onboarding diagnostic",
        "primary_concept_id": concept_id,
        "has_graded_outcome": True,
        "attempt_source": "onboarding",
        "student_answer": "1" if is_correct else "0",
        "is_correct": is_correct,
        "error_type": None,
        "time_taken_sec": None,
        "retrieved_chunks": [chunk] if chunk else [],
        "interaction_count": 1,
    }

    diagnostic_node(state)

    states = db.read_all_user_concept_states(user_id)
    concept_scores = {
        row["concept_id"]: float(row.get("proficiency_score", 0.5))
        for row in states
    }
    overall = (
        sum(concept_scores.values()) / len(concept_scores)
        if concept_scores
        else 0.5
    )

    return {
        "concept_scores": concept_scores,
        "overall": round(overall, 4),
    }
