"""Mock-test HTTP routes.

This router is not mounted on the main FastAPI app. The existing tutor
server does not import this module. To turn the routes on later, add this
to api_server.py:

    from mock_test_agent.router import router as mock_test_router
    app.include_router(mock_test_router)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.supabase_client import db

router = APIRouter(prefix="/api/v1/mock", tags=["mock-test"])

# ══════════════════════════════════════════════════════════════════════════════
# Mock Test API  —  /api/v1/mock/*
# ══════════════════════════════════════════════════════════════════════════════
#
# Lifecycle:
#   GET  /catalog          → chapter list with gradeable PYQ counts (no auth needed beyond user_id)
#   POST /build            → validate config, select questions, persist; returns sections (no answers)
#   POST /start            → mark in_progress, return questions for the timed session
#   POST /answer           → save one student answer (no grade revealed yet)
#   POST /submit           → grade everything, compute scores, update proficiency
#   GET  /result/{id}      → full analytics + optional LLM debrief narrative
#
# All heavy lifting lives in mock_test_agent/assembly.py (assembly) and
# mock_test_agent/grading.py (scoring). This layer only handles HTTP, auth, and
# error mapping.
# ─────────────────────────────────────────────────────────────────────────────

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class MockSubjectConfig(BaseModel):
    subject: str                        # 'Physics' | 'Chemistry' | 'Mathematics'
    chapters: List[str] = []           # chapter_ids; empty = all chapters in subject
    n_questions: int = 30
    difficulty_target: Optional[int] = None


class MockBuildRequest(BaseModel):
    user_id: str
    exam: str = "JEE_ADVANCED"         # JEE_ADVANCED | JEE_MAIN | custom
    subjects: List[MockSubjectConfig]
    total_time_minutes: int = 180
    marking_scheme: Optional[Dict[str, Any]] = None
    exclude_seen: bool = False
    force_new: bool = False
    allow_fallback: bool = True


class MockStartRequest(BaseModel):
    user_id: str
    mock_test_id: str


class MockAnswerRequest(BaseModel):
    user_id: str
    mock_test_id: str
    content_id: str
    student_answer: Optional[str] = None   # raw text; always set for numeric
    choice: Optional[List[str]] = None     # ['A', 'C'] for MCQ
    numeric_value: Optional[float] = None  # parsed float for numeric types
    time_taken_sec: Optional[int] = None


class MockSubmitRequest(BaseModel):
    user_id: str
    mock_test_id: str


# ── /mock/catalog ─────────────────────────────────────────────────────────────

@router.get("/catalog")
def mock_catalog(
    user_id: str,
    subject: Optional[str] = None,
    exam: str = "JEE_ADVANCED",
) -> Dict[str, Any]:
    """Return chapters with their gradeable PYQ counts.

    Used by the frontend to let students pick which chapters to include and
    see whether a chapter has enough questions for a meaningful test.
    """
    from retrieval import store as ret_store
    from mock_test_agent import settings as cfg

    exam_filter = cfg.EXAM_ALLOWED_DB.get(exam, ())
    all_chapters = ret_store.chapters()

    if subject:
        all_chapters = [ch for ch in all_chapters if ch.get("subject") == subject]

    results = []
    for ch in all_chapters:
        ch_id = ch["chapter_id"]
        params: Dict[str, Any] = {"chapter_id": ch_id}
        exam_clause = ""
        if exam_filter:
            params["exams"] = list(exam_filter)
            exam_clause = "AND c.target_exam = ANY(%(exams)s)"
        try:
            rows = ret_store.query(
                f"""
                SELECT count(*) AS n
                FROM public.content c
                JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
                WHERE c.chapter_id = %(chapter_id)s
                  AND c.content_type = 'PYQ'
                  AND c.question_type NOT IN ('subjective','matching')
                  AND sol.answer IS NOT NULL
                  {exam_clause}
                """,
                params,
            )
            count = int(rows[0]["n"]) if rows else 0
        except Exception:
            count = 0

        results.append({
            "chapter_id": ch_id,
            "title": ch.get("title", ch_id),
            "subject": ch.get("subject", ""),
            "in_syllabus": bool(ch.get("in_syllabus", True)),
            "gradeable_pyqs": count,
            "recommended_max": min(count, cfg.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT),
        })

    return {
        "exam": exam,
        "chapters": results,
        "subjects": sorted({c["subject"] for c in results if c["subject"]}),
    }


# ── /mock/build ───────────────────────────────────────────────────────────────

@router.post("/build")
def mock_build(body: MockBuildRequest) -> Dict[str, Any]:
    """Validate the config, select questions, persist the test.

    Returns the assembled test (questions without answers) and a feasibility
    report explaining any chapter shortfalls.
    """
    from mock_test_agent.models import MockTestConfig, SubjectConfig
    from mock_test_agent.assembly import MockTestConfigError, assemble_test

    if not body.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")

    cfg_obj = MockTestConfig(
        user_id=body.user_id.strip(),
        exam=body.exam,
        subjects=[
            SubjectConfig(
                subject=s.subject,
                chapters=s.chapters,
                n_questions=s.n_questions,
                difficulty_target=s.difficulty_target,
            )
            for s in body.subjects
        ],
        total_time_minutes=body.total_time_minutes,
        marking_scheme=body.marking_scheme,
        exclude_seen=body.exclude_seen,
        force_new=body.force_new,
        allow_fallback=body.allow_fallback,
    )

    try:
        result = assemble_test(cfg_obj)
    except MockTestConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logging.getLogger("aeropt.mock").error("mock build failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Test assembly failed: {exc}")

    return result


# ── /mock/start ───────────────────────────────────────────────────────────────

@router.post("/start")
def mock_start(body: MockStartRequest) -> Dict[str, Any]:
    """Mark the test as in_progress and return the full question list.

    The answer field is stripped — students never see answers before submit.
    """
    from datetime import datetime, timezone
    from mock_test_agent.assembly import get_test, get_test_questions

    test = get_test(body.mock_test_id, body.user_id)
    if not test:
        raise HTTPException(status_code=404, detail="Mock test not found")
    if test["status"] == "submitted" or test["status"] == "graded":
        raise HTTPException(status_code=409, detail="Test has already been submitted")

    now = datetime.now(timezone.utc).isoformat()
    try:
        db.client.table("mock_tests").update({
            "status": "in_progress",
            "started_at": now,
        }).eq("mock_test_id", body.mock_test_id).execute()
    except Exception:
        logging.getLogger("aeropt.mock").warning("could not update status to in_progress", exc_info=True)

    questions = get_test_questions(body.mock_test_id, include_answers=False)
    return {
        "mock_test_id": body.mock_test_id,
        "status": "in_progress",
        "started_at": now,
        "time_limit_minutes": test.get("time_limit_minutes", 180),
        "total_questions": len(questions),
        "questions": questions,
    }


# ── /mock/answer ──────────────────────────────────────────────────────────────

@router.post("/answer")
def mock_answer(body: MockAnswerRequest) -> Dict[str, Any]:
    """Save one student answer.  Grade is not revealed until /submit.

    Idempotent: re-submitting the same content_id overwrites the previous answer
    as long as the test has not been submitted.
    """
    from mock_test_agent.assembly import get_test, save_answer

    test = get_test(body.mock_test_id, body.user_id)
    if not test:
        raise HTTPException(status_code=404, detail="Mock test not found")
    if test["status"] in ("submitted", "graded"):
        raise HTTPException(status_code=409, detail="Test has already been submitted; answers cannot be changed")
    if test["status"] == "built":
        raise HTTPException(status_code=409, detail="Call /mock/start before submitting answers")

    result = save_answer(
        mock_test_id=body.mock_test_id,
        content_id=body.content_id,
        user_id=body.user_id,
        student_answer=body.student_answer or "",
        choice=body.choice,
        numeric_value=body.numeric_value,
        time_taken_sec=body.time_taken_sec,
    )
    return result


# ── /mock/submit ──────────────────────────────────────────────────────────────

@router.post("/submit")
def mock_submit(body: MockSubmitRequest) -> Dict[str, Any]:
    """Grade all answers, compute analytics, update proficiency, persist scores.

    Idempotent: a second call returns the already-computed result without
    re-grading (to avoid double-counting proficiency updates).
    """
    from datetime import datetime, timezone
    from mock_test_agent.assembly import get_test, get_test_questions, get_submissions
    from mock_test_agent.grading import resolve_marking_scheme, compute_result

    test = get_test(body.mock_test_id, body.user_id)
    if not test:
        raise HTTPException(status_code=404, detail="Mock test not found")
    if test["status"] == "graded":
        # Already graded — return stored result without re-grading
        return {
            "mock_test_id": body.mock_test_id,
            "status": "graded",
            "already_submitted": True,
            "raw_score": test.get("raw_score"),
            "max_score": test.get("max_score"),
            "accuracy_pct": test.get("accuracy_pct"),
        }

    now = datetime.now(timezone.utc).isoformat()
    try:
        db.client.table("mock_tests").update({
            "status": "submitted",
            "submitted_at": now,
        }).eq("mock_test_id", body.mock_test_id).execute()
    except Exception:
        logging.getLogger("aeropt.mock").warning("could not mark as submitted", exc_info=True)

    questions = get_test_questions(body.mock_test_id, include_answers=True)
    submissions = get_submissions(body.mock_test_id, body.user_id)

    # Use a synthetic session_id for commit_progress (mock tests don't have a chat session)
    session_id = f"mock_{body.mock_test_id}"

    config_raw = test.get("config") or {}
    custom_scheme = config_raw.get("marking_scheme") if isinstance(config_raw, dict) else None

    try:
        result = compute_result(
            mock_test_id=body.mock_test_id,
            user_id=body.user_id,
            session_id=session_id,
            test_row=test,
            questions=questions,
            submissions=submissions,
            custom_scheme=custom_scheme,
        )
    except Exception as exc:
        logging.getLogger("aeropt.mock").error("compute_result failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Grading failed: {exc}")

    return {"submitted": True, **result}


# ── /mock/result ──────────────────────────────────────────────────────────────

@router.get("/result/{mock_test_id}")
def mock_result(mock_test_id: str, user_id: str, include_debrief: bool = False) -> Dict[str, Any]:
    """Return the full result for a graded mock test.

    If include_debrief=true, triggers the LLM debrief narrative (cached after
    first generation).  The result is always returned even if debrief generation
    fails or is skipped.
    """
    from mock_test_agent.assembly import get_test, get_test_questions, get_submissions

    test = get_test(mock_test_id, user_id)
    if not test:
        raise HTTPException(status_code=404, detail="Mock test not found")
    if test["status"] not in ("submitted", "graded"):
        raise HTTPException(
            status_code=409,
            detail=f"Test is not yet submitted (status={test['status']!r}). Call /mock/submit first.",
        )

    # Build result from stored aggregates (no re-grading)
    questions = get_test_questions(mock_test_id, include_answers=False)
    submissions = get_submissions(mock_test_id, user_id)

    sub_map = {s["content_id"]: s for s in submissions}
    graded_questions = []
    for q in questions:
        sub = sub_map.get(q["content_id"])
        graded_questions.append({
            "content_id": q["content_id"],
            "display_order": q.get("display_order"),
            "section": q.get("section"),
            "chapter_id": q.get("chapter_id"),
            "chapter_title": q.get("chapter_title"),
            "concept_id": q.get("concept_id"),
            "concept_name": q.get("concept_name"),
            "question_type": q.get("question_type"),
            "difficulty": q.get("difficulty"),
            "stem": q.get("stem"),
            "options": q.get("options"),
            "grade_status": (sub or {}).get("grade_status"),
            "is_correct": (sub or {}).get("is_correct"),
            "question_score": (sub or {}).get("question_score"),
            "answer_text": (sub or {}).get("answer_text"),
            "student_answer": (sub or {}).get("student_answer"),
        })

    result: Dict[str, Any] = {
        "mock_test_id": mock_test_id,
        "status": test["status"],
        "exam": test.get("exam"),
        "time_limit_minutes": test.get("time_limit_minutes"),
        "started_at": test.get("started_at"),
        "submitted_at": test.get("submitted_at"),
        "overall": {
            "raw_score": test.get("raw_score"),
            "max_score": test.get("max_score"),
            "accuracy_pct": test.get("accuracy_pct"),
            "questions_total": len(questions),
            "questions_attempted": sum(
                1 for s in submissions
                if s.get("grade_status") not in ("unattempted", "ungradable", None)
            ),
            "questions_correct": sum(1 for s in submissions if s.get("is_correct")),
        },
        "subject_scores": test.get("subject_scores") or {},
        "chapter_scores": test.get("chapter_scores") or {},
        "graded_questions": graded_questions,
        "debrief": test.get("debrief"),
    }

    # Optionally trigger (or return cached) LLM debrief
    if include_debrief and not test.get("debrief"):
        try:
            from mock_test_agent.debrief import generate_debrief
            debrief_text = generate_debrief(
                mock_test_id=mock_test_id,
                user_id=user_id,
                test_row=test,
                graded_questions=graded_questions,
                subject_scores=result["subject_scores"],
                chapter_scores=result["chapter_scores"],
            )
            result["debrief"] = debrief_text
        except Exception:
            logging.getLogger("aeropt.mock").warning("debrief generation failed", exc_info=True)
            result["debrief"] = None

    return result

