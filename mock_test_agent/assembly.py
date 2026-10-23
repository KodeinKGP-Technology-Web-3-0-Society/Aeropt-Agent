"""Mock Test assembly pipeline — entirely deterministic, no LLM.

Responsibilities:
  validate_config        Check the incoming MockTestConfig for correctness.
  check_inventory        Count gradeable PYQs available per chapter; return a
                         FeasibilityReport without touching the user database.
  select_questions       Retrieve questions for one subject section using the
                         existing retrieval/search.py pipeline.
  assemble_test          Orchestrate the full build: validate → inventory →
                         select per section → persist to mock_tests +
                         mock_test_questions → return the built test dict.
  build_feasibility_report  Public helper that returns the inventory check
                             result without actually assembling a test.

Only PYQs with a discrete answer key (has_solution=True, has_gradekey=True)
are eligible for inclusion.  Practice Questions (NCERT/HC Verma/OpenStax)
have no answer keys and are excluded from mock tests.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from db.supabase_client import db
from mock_test_agent import settings as config
from mock_test_agent.models import (
    FeasibilityGap,
    FeasibilityReport,
    MockTestConfig,
    MockTestQuestion,
    MockTestSection,
    SubjectConfig,
)
from retrieval import search, store

logger = logging.getLogger("aeropt.mock_test")

# Question types that cannot be auto-graded in v1 — excluded from mock tests.
_UNGRADEABLE_TYPES = frozenset({"subjective", "matching"})
# Comprehension questions share a passage; include them but flag appropriately.
_GRADEABLE_TYPES = frozenset({
    "single_correct", "numeric", "multiple_correct",
    "integer", "comprehension",
})

VALID_EXAMS = frozenset({"JEE_ADVANCED", "JEE_MAIN", "custom"})
VALID_SUBJECTS = frozenset({"Physics", "Chemistry", "Mathematics"})

# How many extra candidates to fetch per chapter to absorb post-filter losses.
_CANDIDATE_BUFFER_FACTOR = 1.4


# ── Validation ────────────────────────────────────────────────────────────────

class MockTestConfigError(ValueError):
    """Raised when the config is invalid and cannot be used."""


def validate_config(cfg: MockTestConfig) -> None:
    """Raise MockTestConfigError if the config is invalid.

    Does not touch the database.
    """
    if not cfg.user_id or not cfg.user_id.strip():
        raise MockTestConfigError("user_id is required")
    if cfg.exam not in VALID_EXAMS:
        raise MockTestConfigError(f"exam must be one of {sorted(VALID_EXAMS)}, got {cfg.exam!r}")
    if not cfg.subjects:
        raise MockTestConfigError("at least one subject section is required")

    seen_subjects: set[str] = set()
    for s in cfg.subjects:
        if s.subject not in VALID_SUBJECTS:
            raise MockTestConfigError(
                f"subject must be one of {sorted(VALID_SUBJECTS)}, got {s.subject!r}"
            )
        if s.subject in seen_subjects:
            raise MockTestConfigError(f"duplicate subject section: {s.subject!r}")
        seen_subjects.add(s.subject)

        if s.n_questions < 1:
            raise MockTestConfigError(
                f"{s.subject}: n_questions must be >= 1, got {s.n_questions}"
            )
        if s.n_questions > config.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT:
            raise MockTestConfigError(
                f"{s.subject}: n_questions exceeds maximum "
                f"({config.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT})"
            )
        if s.difficulty_target is not None and s.difficulty_target not in range(1, 6):
            raise MockTestConfigError(
                f"{s.subject}: difficulty_target must be 1–5 or null"
            )

    if cfg.total_time_minutes < 10 or cfg.total_time_minutes > 360:
        raise MockTestConfigError("total_time_minutes must be between 10 and 360")

    # Validate custom marking scheme keys if provided
    if cfg.marking_scheme:
        allowed_qtype_keys = {
            "single_correct", "multiple_correct", "numeric", "integer", "comprehension",
        }
        allowed_score_keys = {"correct", "incorrect", "unattempted", "partial"}
        for qtype, scheme in cfg.marking_scheme.items():
            if qtype not in allowed_qtype_keys:
                raise MockTestConfigError(
                    f"marking_scheme has unknown question type: {qtype!r}"
                )
            for k in scheme:
                if k not in allowed_score_keys:
                    raise MockTestConfigError(
                        f"marking_scheme[{qtype!r}] has unknown key: {k!r}"
                    )


# ── Chapter / concept catalogue helpers ──────────────────────────────────────

def _all_chapter_map() -> Dict[str, Dict[str, Any]]:
    """chapter_id → chapter row (from retrieval store cache)."""
    return {ch["chapter_id"]: ch for ch in store.chapters()}


def _resolve_chapter_ids(subj_cfg: SubjectConfig) -> List[str]:
    """Return the validated chapter_id list for a subject section.

    If `chapters` is empty, return all chapters for that subject.
    Silently drops unrecognised chapter_ids with a warning.
    """
    chapter_map = _all_chapter_map()
    subject_chapters = [
        ch_id
        for ch_id, ch in chapter_map.items()
        if ch.get("subject") == subj_cfg.subject
    ]

    if not subj_cfg.chapters:
        return subject_chapters

    valid: List[str] = []
    for ch_id in subj_cfg.chapters:
        if ch_id in chapter_map:
            if chapter_map[ch_id].get("subject") == subj_cfg.subject:
                valid.append(ch_id)
            else:
                logger.warning(
                    "chapter %r does not belong to subject %r — skipping",
                    ch_id,
                    subj_cfg.subject,
                )
        else:
            logger.warning("chapter_id %r not found in catalogue — skipping", ch_id)

    return valid if valid else subject_chapters


# ── Inventory check (read-only, no mutations) ─────────────────────────────────

def _count_gradeable_pyqs(chapter_id: str, exam: str) -> int:
    """Count PYQs that have a discrete answer key for a given chapter and exam.

    Uses a direct Postgres query against the retrieval DB to get an accurate
    count without embedding (much faster than search()).
    """
    exam_filter = config.EXAM_ALLOWED_DB.get(exam, ())

    params: Dict[str, Any] = {"chapter_id": chapter_id}
    exam_clause = ""
    if exam_filter:
        params["exams"] = list(exam_filter)
        exam_clause = "AND c.target_exam = ANY(%(exams)s)"

    try:
        rows = store.query(
            f"""
            SELECT count(*) AS n
            FROM public.content c
            JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
            WHERE c.chapter_id = %(chapter_id)s
              AND c.content_type = 'PYQ'
              AND c.question_type NOT IN ('subjective', 'matching')
              AND sol.answer IS NOT NULL
              {exam_clause}
            """,
            params,
        )
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        logger.warning("inventory count failed for chapter %r", chapter_id, exc_info=True)
        return 0


def check_inventory(cfg: MockTestConfig) -> FeasibilityReport:
    """Return the number of gradeable PYQs available vs. requested.

    Does not modify any database table.
    """
    chapter_map = _all_chapter_map()
    total_requested = 0
    total_available = 0
    gaps: List[FeasibilityGap] = []
    warnings: List[str] = []

    for subj_cfg in cfg.subjects:
        chapter_ids = _resolve_chapter_ids(subj_cfg)
        n_chapters = len(chapter_ids)
        if n_chapters == 0:
            warnings.append(f"No chapters found for subject {subj_cfg.subject!r}")
            total_requested += subj_cfg.n_questions
            continue

        # Distribute questions roughly evenly across chapters (then top up from largest)
        per_chapter = max(1, subj_cfg.n_questions // n_chapters)
        remainder = subj_cfg.n_questions - per_chapter * n_chapters

        for i, ch_id in enumerate(chapter_ids):
            allocated = per_chapter + (1 if i < remainder else 0)
            total_requested += allocated
            available = _count_gradeable_pyqs(ch_id, cfg.exam)
            total_available += min(available, allocated)

            if available < allocated:
                ch = chapter_map.get(ch_id, {})
                gaps.append(FeasibilityGap(
                    chapter_id=ch_id,
                    chapter_title=ch.get("title", ch_id),
                    subject=subj_cfg.subject,
                    requested=allocated,
                    available=available,
                ))

    return FeasibilityReport(
        requested=total_requested,
        available=total_available,
        gaps=gaps,
        warnings=warnings,
    )


def build_feasibility_report(cfg: MockTestConfig) -> Dict[str, Any]:
    """Public entry point for the /mock/catalog feasibility check."""
    validate_config(cfg)
    report = check_inventory(cfg)
    return report.to_dict()


# ── Question selection ────────────────────────────────────────────────────────

def _difficulty_for_chapter(subj_cfg: SubjectConfig, exam: str) -> int:
    """Difficulty target: subj override > exam default > 3."""
    if subj_cfg.difficulty_target is not None:
        return subj_cfg.difficulty_target
    return config.MOCK_TEST_DIFFICULTY_DEFAULTS.get(exam, 3)


def _select_for_chapter(
    *,
    chapter_id: str,
    chapter_map: Dict[str, Dict[str, Any]],
    n_wanted: int,
    exam: str,
    difficulty_target: int,
    exclude_ids: set,
    exclude_fingerprints: set,
    section_order_offset: int,
) -> Tuple[List[MockTestQuestion], int]:
    """Fetch up to `n_wanted` gradeable PYQs from one chapter.

    Returns (list_of_MockTestQuestion, count_used) where count_used = len(list).
    display_order is filled starting from section_order_offset.
    """
    ch = chapter_map.get(chapter_id, {})
    concept_ids_in_chapter: List[str] = [
        c["concept_id"]
        for c in store.all_concepts()
        if c.get("chapter_id") == chapter_id
    ]
    if not concept_ids_in_chapter:
        return [], 0

    # Use the first concept as the query anchor so search() scopes correctly.
    anchor_concept = concept_ids_in_chapter[0]
    top_k = max(n_wanted, int(n_wanted * _CANDIDATE_BUFFER_FACTOR) + 5)

    try:
        result = search.search(
            ch.get("title", chapter_id),   # query text = chapter title
            concept_id=anchor_concept,
            content_type="pyq",
            chapter_concepts=concept_ids_in_chapter,
            target_exam=exam,
            top_k=top_k,
            exclude_ids=exclude_ids,
            exclude_fingerprints=exclude_fingerprints,
            difficulty_target=difficulty_target,
            require_solution=True,
            in_syllabus_only=False,   # take all — syllabus filter is for tutor, not mock
            diversify=True,
            prefer_official=False,
        )
    except Exception:
        logger.warning("search failed for chapter %r", chapter_id, exc_info=True)
        return [], 0

    questions: List[MockTestQuestion] = []
    used_concepts: set = set()

    for hit in result.hits:
        if len(questions) >= n_wanted:
            break
        chunk = hit.as_chunk()

        qtype = chunk.get("question_type") or ""
        if qtype in _UNGRADEABLE_TYPES:
            continue

        has_gradekey = bool(chunk.get("has_solution")) and bool(chunk.get("answer"))

        # Parse structured JSON if it came as a string
        structured = chunk.get("structured")
        if isinstance(structured, str):
            try:
                structured = json.loads(structured)
            except (ValueError, TypeError):
                structured = {}
        structured = structured or {}

        stem = structured.get("stem") or chunk.get("core_text") or ""
        options_raw = structured.get("options")
        options: Optional[List[Dict[str, str]]] = None
        if isinstance(options_raw, list):
            options = [{"label": o.get("label", ""), "text": o.get("text", "")} for o in options_raw]

        concept_id = chunk.get("concept_id") or anchor_concept
        concept_row = store.concept(concept_id) or {}
        used_concepts.add(concept_id)

        # Snapshot the answer for storage (it is never sent to the student)
        answer_raw = chunk.get("answer")
        if isinstance(answer_raw, str):
            try:
                answer_raw = json.loads(answer_raw)
            except (ValueError, TypeError):
                answer_raw = None

        q = MockTestQuestion(
            content_id=chunk["content_id"],
            display_order=section_order_offset + len(questions) + 1,
            section=ch.get("subject", ""),
            chapter_id=chapter_id,
            chapter_title=ch.get("title", chapter_id),
            concept_id=concept_id,
            concept_name=concept_row.get("name", concept_id),
            question_type=qtype,
            difficulty=int(chunk.get("difficulty") or 2),
            stem=stem[:4000],           # guard against very long passages
            options=options,
            has_gradekey=has_gradekey,
        )
        # Attach the answer snapshot as a private attribute for the DB insert
        q._answer_raw = answer_raw  # type: ignore[attr-defined]
        questions.append(q)
        # Track used IDs for dedup within this test build
        exclude_ids.add(chunk["content_id"])
        if chunk.get("fingerprint"):
            exclude_fingerprints.add(chunk["fingerprint"])

    return questions, len(questions)


def select_questions(
    subj_cfg: SubjectConfig,
    *,
    exam: str,
    exclude_ids: set,
    exclude_fingerprints: set,
    order_start: int = 1,
    allow_fallback: bool = True,
) -> MockTestSection:
    """Build the question list for one subject section.

    Questions are distributed across chapters proportionally. If a chapter
    yields fewer than its allocation and allow_fallback=True, the deficit is
    drawn from the chapter with the largest surplus pool (same subject).
    """
    chapter_map = _all_chapter_map()
    chapter_ids = _resolve_chapter_ids(subj_cfg)
    difficulty_target = _difficulty_for_chapter(subj_cfg, exam)
    n_total = subj_cfg.n_questions

    # Distribute across chapters
    n_ch = len(chapter_ids)
    if n_ch == 0:
        return MockTestSection(subject=subj_cfg.subject, chapter_ids=[], questions=[])

    per_chapter = max(1, n_total // n_ch)
    remainder = n_total - per_chapter * n_ch
    allocations = [per_chapter + (1 if i < remainder else 0) for i in range(n_ch)]

    all_questions: List[MockTestQuestion] = []
    shortfalls: Dict[str, int] = {}
    surpluses: Dict[str, int] = {}

    current_order = order_start

    for ch_id, alloc in zip(chapter_ids, allocations):
        qs, got = _select_for_chapter(
            chapter_id=ch_id,
            chapter_map=chapter_map,
            n_wanted=alloc,
            exam=exam,
            difficulty_target=difficulty_target,
            exclude_ids=exclude_ids,
            exclude_fingerprints=exclude_fingerprints,
            section_order_offset=current_order - 1,
        )
        for q in qs:
            q.display_order = current_order
            current_order += 1
        all_questions.extend(qs)

        diff = got - alloc
        if diff < 0:
            shortfalls[ch_id] = -diff
        elif diff > 0:
            surpluses[ch_id] = diff

    # Fallback: fill shortfalls from surplus chapters if allowed
    if allow_fallback and shortfalls:
        total_short = sum(shortfalls.values())
        logger.info(
            "%s: %d questions short across %d chapters; attempting fallback",
            subj_cfg.subject, total_short, len(shortfalls),
        )
        # Fetch extra from the chapters that had a large pool
        fallback_chapters = [
            ch_id for ch_id in chapter_ids if ch_id not in shortfalls
        ]
        for ch_id in fallback_chapters:
            if total_short <= 0:
                break
            extra_want = min(total_short, 10)
            qs, got = _select_for_chapter(
                chapter_id=ch_id,
                chapter_map=chapter_map,
                n_wanted=extra_want,
                exam=exam,
                difficulty_target=difficulty_target,
                exclude_ids=exclude_ids,
                exclude_fingerprints=exclude_fingerprints,
                section_order_offset=current_order - 1,
            )
            for q in qs:
                q.display_order = current_order
                current_order += 1
            all_questions.extend(qs)
            total_short -= got

    return MockTestSection(
        subject=subj_cfg.subject,
        chapter_ids=chapter_ids,
        questions=all_questions,
    )


# ── Persistence helpers ───────────────────────────────────────────────────────

def _persist_test(
    cfg: MockTestConfig,
    sections: List[MockTestSection],
    feasibility: FeasibilityReport,
) -> str:
    """Insert mock_tests + mock_test_questions rows; return mock_test_id."""
    mock_test_id = str(uuid.uuid4())
    total_q = sum(len(s.questions) for s in sections)

    db.client.table("mock_tests").insert({
        "mock_test_id": mock_test_id,
        "user_id": cfg.user_id,
        "exam": cfg.exam,
        "config": cfg.to_dict(),
        "status": "built",
        "total_questions": total_q,
        "questions_requested": cfg.total_questions,
        "time_limit_minutes": cfg.total_time_minutes,
        "subject_scores": {},
        "chapter_scores": {},
    }).execute()

    # Bulk-insert questions
    rows = []
    for section in sections:
        for q in section.questions:
            rows.append({
                "mock_test_id": mock_test_id,
                "content_id": q.content_id,
                "display_order": q.display_order,
                "section": q.section,
                "chapter_id": q.chapter_id,
                "chapter_title": q.chapter_title,
                "concept_id": q.concept_id,
                "concept_name": q.concept_name,
                "question_type": q.question_type,
                "difficulty": q.difficulty,
                "stem": q.stem,
                "options": q.options,
                "answer": getattr(q, "_answer_raw", None),   # private snapshot
                "has_gradekey": q.has_gradekey,
            })

    if rows:
        db.client.table("mock_test_questions").insert(rows).execute()

    return mock_test_id


# ── Main entry point ──────────────────────────────────────────────────────────

def assemble_test(cfg: MockTestConfig) -> Dict[str, Any]:
    """Build and persist a mock test.  Returns the full built-test dict.

    Raises MockTestConfigError for invalid configs.
    Raises RuntimeError if the DB write fails.
    """
    validate_config(cfg)

    # Build per-user exclusion sets if requested
    exclude_ids: set = set()
    exclude_fingerprints: set = set()
    if cfg.exclude_seen:
        seen = store.served_ids(cfg.user_id)
        exclude_ids = set(seen.keys())
        exclude_fingerprints = store.served_fingerprints(cfg.user_id)

    feasibility = check_inventory(cfg)
    if feasibility.gaps:
        logger.info(
            "mock test has %d chapter gaps; allow_fallback=%s",
            len(feasibility.gaps),
            cfg.allow_fallback,
        )

    sections: List[MockTestSection] = []
    order_counter = 1
    for subj_cfg in cfg.subjects:
        section = select_questions(
            subj_cfg,
            exam=cfg.exam,
            exclude_ids=exclude_ids,
            exclude_fingerprints=exclude_fingerprints,
            order_start=order_counter,
            allow_fallback=cfg.allow_fallback,
        )
        order_counter += len(section.questions) + 1
        sections.append(section)

    mock_test_id = _persist_test(cfg, sections, feasibility)

    return {
        "mock_test_id": mock_test_id,
        "user_id": cfg.user_id,
        "exam": cfg.exam,
        "status": "built",
        "total_questions": sum(len(s.questions) for s in sections),
        "time_limit_minutes": cfg.total_time_minutes,
        "sections": [s.to_dict() for s in sections],
        "feasibility": feasibility.to_dict(),
    }


# ── Read helpers (used by API routes) ────────────────────────────────────────

def get_test(mock_test_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a mock_tests row.  Returns None if not found or user mismatch."""
    try:
        resp = (
            db.client.table("mock_tests")
            .select("*")
            .eq("mock_test_id", mock_test_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return resp.data or None
    except Exception:
        return None


def get_test_questions(mock_test_id: str, include_answers: bool = False) -> List[Dict[str, Any]]:
    """Fetch all questions for a mock test, ordered by display_order.

    If include_answers=False (default), the `answer` field is stripped.
    """
    try:
        resp = (
            db.client.table("mock_test_questions")
            .select("*")
            .eq("mock_test_id", mock_test_id)
            .order("display_order")
            .execute()
        )
        rows = resp.data or []
        if not include_answers:
            for r in rows:
                r.pop("answer", None)
        return rows
    except Exception:
        logger.warning("get_test_questions failed for %r", mock_test_id, exc_info=True)
        return []


def get_submissions(mock_test_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Fetch all submissions for a mock test."""
    try:
        resp = (
            db.client.table("mock_test_submissions")
            .select("*")
            .eq("mock_test_id", mock_test_id)
            .eq("user_id", user_id)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def save_answer(
    *,
    mock_test_id: str,
    content_id: str,
    user_id: str,
    student_answer: str,
    choice: Optional[List[str]] = None,
    numeric_value: Optional[float] = None,
    time_taken_sec: Optional[int] = None,
    concept_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert one student answer. Grade is filled later on /submit."""
    row = {
        "mock_test_id": mock_test_id,
        "content_id": content_id,
        "user_id": user_id,
        "student_answer": (student_answer or "")[:2000],
        "choice": choice,
        "numeric_value": numeric_value,
        "time_taken_sec": time_taken_sec,
        "grade_status": None,     # filled on submit
        "concept_id": concept_id,
    }
    try:
        db.client.table("mock_test_submissions").upsert(
            row,
            on_conflict="mock_test_id,content_id,user_id",
        ).execute()
        return {"recorded": True}
    except Exception:
        logger.warning("save_answer failed", exc_info=True)
        return {"recorded": False, "error": "database write failed"}
