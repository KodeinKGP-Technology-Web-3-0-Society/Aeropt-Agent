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
    res = (
        db.client.table("content")
        .select(
            "content_id, content_type, chapter, difficulty, core_text, solution, primary_concept_id, image_filename, has_image_core, has_diagram, has_image_solution"
        )
        .eq("primary_concept_id", concept_id)
        .eq("content_type", "practice")
        .limit(5)
        .execute()
    )
    rows = res.data or []
    if not rows:
        res = (
            db.client.table("content")
            .select(
                "content_id, content_type, chapter, difficulty, core_text, solution, primary_concept_id, image_filename, has_image_core, has_diagram, has_image_solution"
            )
            .eq("primary_concept_id", concept_id)
            .limit(3)
            .execute()
        )
        rows = res.data or []
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
        "solution_text": None,
        "solution_image_url": None,
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
    is_correct: Optional[bool] = None,
    answer: Optional[str] = None,
    submission_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Grade a placement answer from its stored key. A client flag is not evidence."""
    del is_correct  # kept so older callers still import; it does not write progress
    _ensure_concept_states(user_id)
    chunk = None
    if content_id:
        rows = db.read_content_by_ids([content_id])
        chunk = rows[0] if rows else None

    grade = None
    if chunk and (answer or "").strip():
        from services.answer_key import grade_free_answer
        from services.grading import answer_fingerprint, commit_progress

        decision = grade_free_answer(chunk, answer)
        if decision["grade_status"] in {"correct", "incorrect"}:
            instance = f"onboarding:{content_id}"
            grade = commit_progress(
                user_id=user_id,
                session_id="",
                concept_id=concept_id,
                content_id=content_id,
                submission_id=(submission_id or "").strip() or instance,
                question_instance_id=instance,
                student_answer=answer or "",
                fingerprint=answer_fingerprint({
                    "student_answer": answer or "",
                    "content_id": content_id,
                    "question_instance_id": instance,
                }),
                is_correct=bool(decision["is_correct"]),
                grade_status=decision["grade_status"],
                evidence_source="answer_key",
                source="onboarding",
                answer_text=decision.get("answer_text") or "",
            ).to_public()
        else:
            from services.grading import GradeResult

            held = GradeResult(
                grade_status=decision["grade_status"],
                evidence_source="none",
                persistence_status="not_attempted",
                content_id=content_id,
                concept_id=concept_id,
                failure_reason=decision.get("failure_reason"),
                answer_text=decision.get("answer_text") or "",
            )
            held.student_message = (
                "This placement answer could not be checked, so your progress was not changed."
            )
            grade = held.to_public()
    elif not (answer or "").strip():
        from services.grading import GradeResult

        held = GradeResult(
            grade_status="ungradable",
            evidence_source="none",
            persistence_status="not_attempted",
            failure_reason="answer_required",
            content_id=content_id or None,
            concept_id=concept_id,
        )
        held.student_message = (
            "Send the answer itself. A correct or incorrect flag is not used, "
            "and your progress was not changed."
        )
        grade = held.to_public()

    from services.grading import measured_score

    states = db.read_all_user_concept_states(user_id)
    concept_scores = {}
    for row in states:
        score = measured_score(row)
        if score is not None:
            concept_scores[row["concept_id"]] = score
    overall = (
        round(sum(concept_scores.values()) / len(concept_scores), 4)
        if concept_scores else None
    )
    return {
        "concept_scores": concept_scores,
        "overall": overall,
        "measured": bool(concept_scores),
        "grade": grade,
    }
