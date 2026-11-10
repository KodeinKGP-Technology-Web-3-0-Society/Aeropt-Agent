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
    concepts: List[str] = []           # concept_ids; empty = every concept in the chapters
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
    mock_type: str = "custom"             # smart | custom | pyq
    coverage: str = "all"                 # learned | all
    year: Optional[int] = None
    paper: Optional[int] = None
    paper_key: Optional[str] = None
    pyq_length: str = "full"               # full | short
    short_questions: Optional[int] = None
    blueprint_id: Optional[str] = None


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
    offers an empty chapter. Ids are the question bank's own chapter ids. A separate
    syllabus list used different ids and every build of those chapters failed.
    """
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
        # Textbook rows have no exam label. Main mode still hides Advanced-only papers.
        if exam == "JEE_MAIN":
            exam_clause = "AND (c.target_exam IS NULL OR c.target_exam IN ('Jee main', 'Jee main and Advanced'))"
        else:
            exam_clause = "AND (c.target_exam IS NULL OR c.target_exam = ANY(%(exams)s) OR c.target_exam = 'Jee main')"
    if stored_types:
        params["qtypes"] = stored_types
        exam_clause += " AND lower(c.question_type) = ANY(%(qtypes)s)"
    try:
        rows = ret_store.query(
            f"""
            SELECT c.chapter_id,
                   count(*) AS n,
                   count(*) FILTER (
                     WHERE c.content_type = 'PYQ' AND c.license ILIKE '%%official exam paper%%'
                   ) AS official
            FROM public.content c
            JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
            WHERE c.content_type IN ('PYQ', 'Practice Question', 'Solved Example')
              AND c.question_type NOT IN ('subjective','matching')
              AND sol.answer IS NOT NULL
              AND (sol.answer ? 'kind')
              AND {ret_store.FIGURE_READY_SQL}
              {exam_clause}
            GROUP BY c.chapter_id
            """,
            params,
        )
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("mock catalog: question counts failed: %s", exc)
        raise HTTPException(status_code=503, detail="Question counts are unavailable right now. Please try again shortly.")
    counts = {r["chapter_id"]: r for r in rows}

    results = []
    for ch in all_chapters:
        row = counts.get(ch["chapter_id"]) or {}
        count = int(row.get("n") or 0)
        official = int(row.get("official") or 0)
        if count <= 0 and official <= 0:
            continue
        results.append({
            "chapter_id": ch["chapter_id"],
            "title": ch.get("title", ch["chapter_id"]),
            "subject": ch.get("subject", ""),
            "in_syllabus": bool(ch.get("in_syllabus", True)),
            "gradeable": count,
            "gradeable_pyqs": official,
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


def _config_from_body(body: MockBuildRequest):
    from mock_test_agent.models import MockTestConfig, SubjectConfig
    return MockTestConfig(
        user_id=body.user_id.strip(),
        exam=body.exam,
        subjects=[
            SubjectConfig(
                subject=s.subject,
                chapters=s.chapters,
                concepts=list(s.concepts or []),
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
        mock_type=body.mock_type or "custom",
        coverage="learned" if body.mock_type == "smart" else (body.coverage or "all"),
        year=body.year,
        paper=body.paper,
        paper_key=body.paper_key,
        pyq_length=body.pyq_length or "full",
        short_questions=body.short_questions,
        blueprint_id=body.blueprint_id,
    )


def _require_test(mock_test_id: str, user_id: str) -> Dict[str, Any]:
    from mock_test_agent.assembly import get_test
    test = get_test(mock_test_id, user_id)
    if not test:
        raise HTTPException(status_code=404, detail="Mock test not found")
    return test


def _public_questions(questions: List[Dict[str, Any]], test: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attach section instructions. Never include the stored answer."""
    cfg = test.get("config") if isinstance(test.get("config"), dict) else {}
    meta = cfg.get("question_meta") if isinstance(cfg, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    out = []
    for q in questions:
        row = dict(q)
        row.pop("answer", None)
        extra = meta.get(row.get("content_id")) or {}
        if isinstance(extra, dict):
            row["paper_section"] = extra.get("paper_section") or row.get("section")
            row["section_label"] = extra.get("section_label") or ""
            row["instructions"] = extra.get("instructions") or ""
            row["source_type"] = extra.get("source_type") or ""
            if extra.get("marks"):
                row["marks"] = extra["marks"]
            row.pop("solution", None)
            row.pop("source_year", None)
            row.pop("source_label", None)
        out.append(row)
    return out


def _with_grade_meta(questions: List[Dict[str, Any]], test: Dict[str, Any]) -> List[Dict[str, Any]]:
    cfg = test.get("config") if isinstance(test.get("config"), dict) else {}
    meta = cfg.get("question_meta") if isinstance(cfg, dict) else {}
    if not isinstance(meta, dict):
        return questions
    for q in questions:
        extra = meta.get(q.get("content_id")) or {}
        if not isinstance(extra, dict):
            continue
        if extra.get("marks"):
            q["marks"] = extra["marks"]
        q["previously_seen"] = bool(extra.get("previously_seen"))
    return questions


@router.get("/home")
@_store_guard
def mock_home(user_id: str, exam: str = "JEE_ADVANCED") -> Dict[str, Any]:
    """Learner summary for the three mock modes. Does not assemble a paper."""
    from mock_test_agent.engine import home_summary
    from mock_test_agent.learner import load_states
    from retrieval import store as ret_store

    if exam not in ("JEE_ADVANCED", "JEE_MAIN", "custom"):
        raise HTTPException(status_code=400, detail="exam must be JEE_MAIN or JEE_ADVANCED")
    exam_sql = (
        "(c.target_exam is null or c.target_exam in ('Jee main', 'Jee main and Advanced'))"
        if exam == "JEE_MAIN" else
        "(c.target_exam is null or c.target_exam in ('Jee Advanced', 'Jee main', 'Jee main and Advanced'))"
    )
    try:
        rows = ret_store.query(
            f"""
            SELECT c.subject,
                   count(*) AS gradeable,
                   count(*) FILTER (
                     WHERE c.content_type = 'PYQ' AND c.license ILIKE '%%official exam paper%%'
                   ) AS official_pyqs
            FROM public.content c
            JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
            WHERE c.content_type IN ('PYQ', 'Practice Question', 'Solved Example')
              AND c.question_type NOT IN ('subjective','matching')
              AND sol.answer IS NOT NULL AND (sol.answer ? 'kind')
              AND {ret_store.FIGURE_READY_SQL}
              AND {exam_sql}
            GROUP BY c.subject
            """,
            {},
        )
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("mock home counts failed: %s", exc)
        raise HTTPException(status_code=503, detail="The question bank is unavailable right now.")
    counts = {
        r["subject"]: {"gradeable": int(r["gradeable"]), "official_pyqs": int(r["official_pyqs"])}
        for r in rows if r.get("subject")
    }
    summary = home_summary(learner_rows=load_states(user_id.strip()), subject_counts=counts, exam=exam)
    summary["user_id"] = user_id.strip()
    return summary


@router.get("/papers")
def mock_papers(exam: str = "JEE_ADVANCED") -> Dict[str, Any]:
    """Official papers stored in the bank, plus organizing-IIT status. No guessed institutes."""
    from mock_test_agent.blueprints import organizing_iit
    from mock_test_agent.pool import list_official_papers

    try:
        papers = [p for p in list_official_papers() if p["exam"] == ("JEE_MAIN" if exam == "JEE_MAIN" else "JEE_ADVANCED")]
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("mock papers failed: %s", exc)
        raise HTTPException(status_code=503, detail="Stored JEE papers are unavailable right now.")
    for paper in papers:
        paper["organizing_iit"] = organizing_iit(paper["year"]) if paper["exam"] == "JEE_ADVANCED" else {
            "iit": "", "status": "not_applicable", "source": "https://jeemain.nta.nic.in/",
        }
    missing = (
        "JEE Main papers in the bank are the 2026 shifts, plus a handful of 2020 Physics questions. "
        "Years 2013–2019 and 2021–2025 are not stored for Main. 2027 is not in the bank."
        if exam == "JEE_MAIN" else
        "JEE Advanced papers in the bank run from 2012 through 2026, for Physics, Chemistry, and Mathematics. "
        "2027 is not stored. Coaching sheets are not included."
    )
    return {"exam": exam, "papers": papers, "limitation": missing}


@router.get("/pyq/syllabus")
def mock_pyq_syllabus(exam: str = "JEE_ADVANCED", user_id: str = "") -> Dict[str, Any]:
    """Chapters and concepts that have genuine PYQs for this exam. Not the student's proficiency."""
    from mock_test_agent.blueprints import public_blueprint, pyq_blueprint
    from mock_test_agent.pool import pyq_syllabus as load_syllabus

    if exam not in ("JEE_MAIN", "JEE_ADVANCED"):
        raise HTTPException(status_code=400, detail="exam must be JEE_MAIN or JEE_ADVANCED")
    try:
        chapters = load_syllabus(exam)
        blueprint = public_blueprint(pyq_blueprint(exam))
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("pyq syllabus failed: %s", exc)
        raise HTTPException(status_code=503, detail="The PYQ syllabus is unavailable right now.")
    return {
        "exam": exam,
        "user_id": user_id,
        "blueprint": blueprint,
        "chapters": chapters,
        "subjects": ["Physics", "Chemistry", "Mathematics"],
        "syllabus_policy": "primary_concept_in_selected_chapters",
    }


@router.post("/pyq/preview")
@_store_guard
def mock_pyq_preview(body: MockBuildRequest) -> Dict[str, Any]:
    """Check that a blueprint can be filled. Does not save a paper or reveal answers."""
    from mock_test_agent.assembly import MockTestConfigError, _seen_ids
    from mock_test_agent.pool import fetch
    from mock_test_agent.pyq_select import preview_from_rows

    if not body.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")
    if body.mock_type != "pyq":
        raise HTTPException(status_code=400, detail="preview is only for JEE PYQ Mock")
    cfg = _config_from_body(body)
    try:
        from mock_test_agent.assembly import validate_config
        validate_config(cfg)
    except MockTestConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        rows = fetch(cfg)
    except Exception as exc:
        logging.getLogger("aeropt.mock").warning("pyq preview fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="The question bank is unavailable right now.")
    return preview_from_rows(cfg, rows, seen=_seen_ids(cfg))


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
    from mock_test_agent.assembly import MockTestConfigError, assemble_test, start_background_assembly

    if not body.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")

    cfg_obj = _config_from_body(body)

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

    questions = _public_questions(get_test_questions(body.mock_test_id, include_answers=False), test)
    if not questions:
        raise HTTPException(status_code=409, detail="This test has no questions. Please build a new one.")
    cfg = test.get("config") if isinstance(test.get("config"), dict) else {}
    return {
        "mock_test_id": body.mock_test_id,
        "status": "in_progress",
        "started_at": started_at,
        "time_limit_minutes": test.get("time_limit_minutes", 180),
        "total_questions": len(questions),
        "questions": questions,
        "answers": _saved_answers(body.mock_test_id, body.user_id),
        "mock_type": (cfg or {}).get("mock_type") or "custom",
        "sectional": bool((cfg or {}).get("sectional")),
        "contains_unlearned": bool((cfg or {}).get("contains_unlearned")),
        "blueprint": (cfg or {}).get("blueprint"),
        "organizing_iit": (cfg or {}).get("organizing_iit"),
        "display_title": (cfg or {}).get("display_title") or "Mock Test",
        "length_label": (cfg or {}).get("length_label") or "",
        "disclaimer": (cfg or {}).get("disclaimer") or "",
        "section_plan": (cfg or {}).get("section_plan") or [],
        "max_marks": (cfg or {}).get("max_marks"),
        "source_years": (cfg or {}).get("source_years") or [],
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
    _reject_if_time_is_up(test)
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

    questions = _with_grade_meta(get_test_questions(body.mock_test_id, include_answers=True), test)
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
    cfg = test.get("config") if isinstance(test.get("config"), dict) else {}
    meta = cfg.get("question_meta") if isinstance(cfg, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    graded_questions = []
    for q in questions:
        sub = sub_map.get(q["content_id"])
        extra = meta.get(q["content_id"]) if isinstance(meta.get(q["content_id"]), dict) else {}
        graded_questions.append({
            "content_id": q["content_id"],
            "display_order": q.get("display_order"),
            "section": q.get("section"),
            "paper_section": extra.get("paper_section") or q.get("section"),
            "chapter_id": q.get("chapter_id"),
            "chapter_title": q.get("chapter_title"),
            "concept_id": q.get("concept_id"),
            "concept_name": q.get("concept_name"),
            "question_type": q.get("question_type"),
            "difficulty": q.get("difficulty"),
            "stem": q.get("stem"),
            "options": q.get("options"),
            "marks": extra.get("marks"),
            "source_year": extra.get("source_year"),
            "source_label": extra.get("source_label"),
            "source_exam": extra.get("source_exam"),
            "solution": extra.get("solution") or "",
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
            "questions_incorrect": sum(1 for s in submissions if s.get("grade_status") == "incorrect"),
        },
        "subject_scores": test.get("subject_scores") or {},
        "chapter_scores": test.get("chapter_scores") or {},
        "section_scores": _section_scores(graded_questions),
        "graded_questions": graded_questions,
        "debrief": test.get("debrief"),
        "recommendations": _recommendations(graded_questions),
        "mock_type": cfg.get("mock_type"),
        "sectional": bool(cfg.get("sectional")),
        "length_label": cfg.get("length_label") or "",
        "blueprint": cfg.get("blueprint"),
        "source_years": cfg.get("source_years") or [],
        "disclaimer": cfg.get("disclaimer") or "",
        "negative_marks": round(sum(
            float(s.get("question_score") or 0) for s in submissions if float(s.get("question_score") or 0) < 0
        ), 2),
    }

    return result


def _reject_if_time_is_up(test: Dict[str, Any]) -> None:
    """The clock is the server start time plus the paper's duration, with a short grace for clock skew."""
    from datetime import datetime, timedelta, timezone
    started = test.get("started_at")
    limit = int(test.get("time_limit_minutes") or 0)
    if not started or limit <= 0:
        return
    try:
        opened = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
    except ValueError:
        return
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > opened + timedelta(minutes=limit, seconds=20):
        raise HTTPException(status_code=409, detail="Time is up. Submit the test.")


def _section_scores(graded: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score each blueprint section from the stored marks. No rank or percentile."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for question in graded:
        key = question.get("paper_section") or question.get("section") or "Paper"
        groups.setdefault(key, []).append(question)
    out = []
    for key, rows in groups.items():
        raw = round(sum(float(q.get("question_score") or 0) for q in rows), 2)
        maximum = round(sum(float((q.get("marks") or {}).get("correct") or 0) for q in rows), 2)
        attempted = [q for q in rows if q.get("grade_status") not in ("unattempted", "ungradable", None)]
        correct = sum(1 for q in attempted if q.get("is_correct"))
        negative = round(sum(float(q.get("question_score") or 0) for q in rows if float(q.get("question_score") or 0) < 0), 2)
        out.append({
            "section": key,
            "raw_score": raw,
            "max_score": maximum,
            "questions_total": len(rows),
            "questions_attempted": len(attempted),
            "questions_correct": correct,
            "questions_incorrect": len(attempted) - correct,
            "unanswered": len(rows) - len(attempted),
            "negative_marks": negative,
        })
    return out


def _recommendations(graded: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Concepts to revise, from this paper only. Does not invent a proficiency change."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for q in graded:
        cid = q.get("concept_id")
        if cid:
            by.setdefault(cid, []).append(q)
    revise = []
    holding = []
    for cid, rows in by.items():
        attempted = [q for q in rows if q.get("grade_status") in ("correct", "incorrect")]
        if not attempted:
            continue
        correct = sum(1 for q in attempted if q.get("is_correct"))
        accuracy = correct / len(attempted)
        item = {
            "concept_id": cid,
            "concept_name": rows[0].get("concept_name") or cid,
            "accuracy_pct": round(accuracy * 100, 1),
            "attempted": len(attempted),
            "study_href": "/study/room",
        }
        if accuracy < 0.5:
            revise.append(item)
        elif accuracy >= 0.75:
            holding.append(item)
    revise.sort(key=lambda item: item["accuracy_pct"])
    return {
        "revise": revise[:5],
        "holding": holding[:5],
        "next": (
            "Your next Smart Mock will spend more of the paper on the concepts you missed, using different questions where the bank has them."
            if revise else
            "Your next Smart Mock can check that the concepts you held here still hold."
        ),
    }


@router.get("/result/{mock_test_id}")
@_store_guard
def mock_result(
    mock_test_id: str,
    user_id: str,
    include_debrief: bool = False,
    regenerate: bool = False,
) -> Dict[str, Any]:
    """Return the full result for a submitted mock test.

    If include_debrief=true, triggers the LLM debrief narrative (cached after
    first generation). regenerate=true writes a new one over the cache.
    The result is always returned even if debrief generation fails or is skipped.
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
    if include_debrief and (regenerate or not test.get("debrief")):
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

