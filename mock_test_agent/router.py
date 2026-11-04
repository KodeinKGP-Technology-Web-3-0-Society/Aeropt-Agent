"""Mock-test HTTP routes.

This router is not mounted on the main FastAPI app. The existing tutor
server does not import this module. To turn the routes on later, add this
to api_server.py:

    from mock_test_agent.router import router as mock_test_router
    app.include_router(mock_test_router)
"""

from __future__ import annotations

import functools
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
    question_types: List[str] = []     # 'single' | 'multiple' | 'numerical'; empty = all


class MockBuildRequest(BaseModel):
    user_id: str
    exam: str = "JEE_ADVANCED"         # JEE_ADVANCED | JEE_MAIN | custom
    subjects: List[MockSubjectConfig]
    total_time_minutes: int = 180
    marking_scheme: Optional[Dict[str, Any]] = None
    exclude_seen: bool = False
    force_new: bool = False
    allow_fallback: bool = True
    async_build: bool = False


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
    types: Optional[str] = None,
) -> Dict[str, Any]:
    """Chapters of the JEE syllabus that have gradeable PYQs, with counts.

    Only chapters a mock test can actually draw from are returned, so the picker never
    offers an empty chapter. If the syllabus cannot be read this fails with 503 rather than
    serving some other chapter list: a wrong syllabus is worse than an error.
    """
    try:
        from mock_test_agent import syllabus_catalog
        chapters = syllabus_catalog.get_standard_catalog(exam=exam)
        if subject:
            chapters = [c for c in chapters if c.get("subject") == subject]
        return {
            "exam": exam,
            "chapters": chapters,
            "subjects": ["Physics", "Chemistry", "Mathematics"],
        }
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("mock catalog fallback to syllabus_catalog failed: %s", exc)

    from retrieval import store as ret_store
    from mock_test_agent import settings as cfg

    exam_filter = cfg.EXAM_ALLOWED_DB.get(exam, ())
    wanted = [t for t in (types or "").split(",") if t.strip()]
    unknown = [t for t in wanted if t not in cfg.QUESTION_TYPE_GROUPS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"types must be among {sorted(cfg.QUESTION_TYPE_GROUPS)}")
    stored_types = sorted({s for t in wanted for s in cfg.QUESTION_TYPE_GROUPS[t]})
    try:
        all_chapters = ret_store.chapters()
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("mock catalog: chapters unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="The syllabus is unavailable right now. Please try again shortly.")
    if subject:
        all_chapters = [ch for ch in all_chapters if ch.get("subject") == subject]

    params: Dict[str, Any] = {}
    exam_clause = ""
    if exam_filter:
        params["exams"] = list(exam_filter)
        exam_clause = "AND c.target_exam = ANY(%(exams)s)"
    if stored_types:
        params["qtypes"] = stored_types
        exam_clause += " AND lower(c.question_type) = ANY(%(qtypes)s)"
    try:
        rows = ret_store.query(
            f"""
            SELECT c.chapter_id, count(*) AS n
            FROM public.content c
            JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
            WHERE c.content_type = 'PYQ'
              AND c.question_type NOT IN ('subjective','matching')
              AND sol.answer IS NOT NULL
              AND {ret_store.FIGURE_READY_SQL}
              {exam_clause}
            GROUP BY c.chapter_id
            """,
            params,
        )
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("mock catalog: question counts failed: %s", exc)
        raise HTTPException(status_code=503, detail="Question counts are unavailable right now. Please try again shortly.")
    counts = {r["chapter_id"]: int(r["n"]) for r in rows}

    results = []
    for ch in all_chapters:
        count = counts.get(ch["chapter_id"], 0)
        if count <= 0:
            continue
        results.append({
            "chapter_id": ch["chapter_id"],
            "title": ch.get("title", ch["chapter_id"]),
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


# ── error mapping ─────────────────────────────────────────────────────────────

def _store_guard(fn):
    """Turn a Supabase outage into a clear 503 instead of a bare 500."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from mock_test_agent.assembly import MockStoreError
        try:
            return fn(*args, **kwargs)
        except MockStoreError as exc:
            logging.getLogger("aeropt.mock").error("mock store error: %s", exc)
            raise HTTPException(status_code=503, detail="Mock tests are temporarily unavailable. Please try again.")
    return wrapper


def _require_test(mock_test_id: str, user_id: str) -> Dict[str, Any]:
    from mock_test_agent.assembly import get_test
    test = get_test(mock_test_id, user_id)
    if not test:
        raise HTTPException(status_code=404, detail="Mock test not found")
    return test


# ── /mock/build ───────────────────────────────────────────────────────────────

@router.post("/build")
@_store_guard
def mock_build(body: MockBuildRequest) -> Dict[str, Any]:
    """Validate the config and build the test.

    async_build=false (default): select and persist everything, return the sections
    (no answers) and a feasibility report.
    async_build=true: create the row as 'building', run the build in the background and
    return immediately; poll GET /mock/status/{id}.
    """
    from mock_test_agent.models import MockTestConfig, SubjectConfig
    from mock_test_agent.assembly import MockTestConfigError, assemble_test, start_background_assembly

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
                question_types=s.question_types,
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
        if body.async_build:
            return start_background_assembly(cfg_obj)
        return assemble_test(cfg_obj)
    except MockTestConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        from mock_test_agent.assembly import MockStoreError
        if isinstance(exc, MockStoreError):
            raise
        logging.getLogger("aeropt.mock").error("mock build failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Test assembly failed: {exc}")


# ── /mock/status ──────────────────────────────────────────────────────────────

@router.get("/status/{mock_test_id}")
@_store_guard
def mock_status(mock_test_id: str, user_id: str) -> Dict[str, Any]:
    """Live build progress (and lifecycle status) of one test."""
    from mock_test_agent.assembly import build_status
    return build_status(_require_test(mock_test_id, user_id))


# ── /mock/history ─────────────────────────────────────────────────────────────

@router.get("/history")
@_store_guard
def mock_history(user_id: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """The caller's mock tests, newest first, with score summaries."""
    from mock_test_agent.assembly import list_tests
    if not user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")
    return list_tests(user_id.strip(), limit=max(1, min(limit, 100)), offset=max(0, offset))


# ── /mock/start ───────────────────────────────────────────────────────────────

def _saved_answers(mock_test_id: str, user_id: str) -> List[Dict[str, Any]]:
    from mock_test_agent.assembly import get_submissions
    return [
        {
            "content_id": s["content_id"],
            "student_answer": s.get("student_answer"),
            "choice": s.get("choice"),
            "numeric_value": s.get("numeric_value"),
        }
        for s in get_submissions(mock_test_id, user_id)
    ]


@router.post("/start")
@_store_guard
def mock_start(body: MockStartRequest) -> Dict[str, Any]:
    """Start the test, or resume it. Idempotent.

    started_at is set only on the first start; a reload of an in_progress test returns the
    original started_at (timer keeps running) plus the student's saved answers. The answer
    key is never included.
    """
    from datetime import datetime, timezone
    from mock_test_agent.assembly import get_test_questions, _table

    test = _require_test(body.mock_test_id, body.user_id)
    status = test["status"]
    if status in ("submitted", "graded"):
        raise HTTPException(status_code=409, detail="Test has already been submitted")
    if status == "building":
        raise HTTPException(status_code=409, detail="The test is still being built. Wait until it is ready.")
    if status == "failed":
        raise HTTPException(
            status_code=409,
            detail=test.get("_build_error") or "The test could not be built. Please build a new one.",
        )

    started_at = test.get("started_at")
    if status == "built":
        now = datetime.now(timezone.utc).isoformat()
        # Conditional on status so two concurrent starts cannot both claim the first start.
        _table("mock_tests").update({"status": "in_progress", "started_at": now}) \
            .eq("mock_test_id", body.mock_test_id).eq("status", "built").execute()
        fresh = _require_test(body.mock_test_id, body.user_id)
        started_at = fresh.get("started_at") or now

    questions = get_test_questions(body.mock_test_id, include_answers=False)
    if not questions:
        raise HTTPException(status_code=409, detail="This test has no questions. Please build a new one.")
    return {
        "mock_test_id": body.mock_test_id,
        "status": "in_progress",
        "started_at": started_at,
        "time_limit_minutes": test.get("time_limit_minutes", 180),
        "total_questions": len(questions),
        "questions": questions,
        "answers": _saved_answers(body.mock_test_id, body.user_id),
    }


# ── /mock/answer ──────────────────────────────────────────────────────────────

@router.post("/answer")
@_store_guard
def mock_answer(body: MockAnswerRequest) -> Dict[str, Any]:
    """Save one student answer.  Grade is not revealed until /submit.

    Idempotent: re-submitting the same content_id overwrites the previous answer
    as long as the test has not been submitted.
    """
    from mock_test_agent.assembly import get_test_questions, save_answer

    test = _require_test(body.mock_test_id, body.user_id)
    status = test["status"]
    if status in ("submitted", "graded"):
        raise HTTPException(status_code=409, detail="Test has already been submitted; answers cannot be changed")
    if status != "in_progress":
        raise HTTPException(status_code=409, detail=f"Test is {status}; call /mock/start before answering")
    if body.content_id not in {q["content_id"] for q in get_test_questions(body.mock_test_id)}:
        raise HTTPException(status_code=404, detail="That question is not part of this test")

    return save_answer(
        mock_test_id=body.mock_test_id,
        content_id=body.content_id,
        user_id=body.user_id,
        student_answer=body.student_answer or "",
        choice=body.choice,
        numeric_value=body.numeric_value,
        time_taken_sec=body.time_taken_sec,
    )


# ── /mock/submit ──────────────────────────────────────────────────────────────

@router.post("/submit")
@_store_guard
def mock_submit(body: MockSubmitRequest) -> Dict[str, Any]:
    """Grade all answers, compute analytics, update proficiency, persist scores.

    Idempotent: a second call returns the already-computed result without
    re-grading (to avoid double-counting proficiency updates).
    """
    from datetime import datetime, timezone
    from mock_test_agent.assembly import _table, get_test_questions, get_submissions
    from mock_test_agent.grading import resolve_marking_scheme, compute_result

    test = _require_test(body.mock_test_id, body.user_id)
    if test["status"] == "graded":
        return {"submitted": True, "already_submitted": True, **_result_payload(test, body.user_id)}
    if test["status"] not in ("in_progress", "submitted"):
        raise HTTPException(status_code=409, detail=f"Test is {test['status']}; it cannot be submitted yet")

    if test["status"] == "in_progress":
        now = datetime.now(timezone.utc).isoformat()
        _table("mock_tests").update({"status": "submitted", "submitted_at": now}) \
            .eq("mock_test_id", body.mock_test_id).eq("status", "in_progress").execute()

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

def _result_payload(test: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Result built from stored rows (no re-grading)."""
    from mock_test_agent.assembly import get_test_questions, get_submissions

    questions = get_test_questions(test['mock_test_id'], include_answers=False)
    submissions = get_submissions(test['mock_test_id'], user_id)

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
            "choice": (sub or {}).get("choice"),
            "numeric_value": (sub or {}).get("numeric_value"),
        })

    result: Dict[str, Any] = {
        "mock_test_id": test["mock_test_id"],
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

    return result


@router.get("/result/{mock_test_id}")
@_store_guard
def mock_result(mock_test_id: str, user_id: str, include_debrief: bool = False) -> Dict[str, Any]:
    """Return the full result for a submitted mock test.

    If include_debrief=true, triggers the LLM debrief narrative (cached after
    first generation).  The result is always returned even if debrief generation
    fails or is skipped.
    """
    test = _require_test(mock_test_id, user_id)
    if test["status"] not in ("submitted", "graded"):
        raise HTTPException(
            status_code=409,
            detail=f"Test is not yet submitted (status={test['status']!r}). Call /mock/submit first.",
        )
    result = _result_payload(test, user_id)
    graded_questions = result["graded_questions"]

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

