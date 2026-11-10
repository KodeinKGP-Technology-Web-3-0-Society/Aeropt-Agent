"""Persist a mock paper. Question choice is engine.compose, not the old PYQ picker.

Smart and Custom use every gradeable question (PYQ, practice, solved example
with an answer key). JEE PYQ Mock uses official Advanced papers only.
Existing mock_tests rows are left in place.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
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
from retrieval import store

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
    if cfg.mock_type not in ("smart", "custom", "pyq"):
        raise MockTestConfigError("mock_type must be smart, custom, or pyq")
    if cfg.coverage not in ("learned", "all"):
        raise MockTestConfigError("coverage must be learned or all")
    if cfg.year is not None and (int(cfg.year) < 2000 or int(cfg.year) > 2100):
        raise MockTestConfigError("year is not a real examination year")
    if cfg.paper is not None and int(cfg.paper) not in (1, 2):
        raise MockTestConfigError("paper must be 1, 2, or omitted")
    if cfg.mock_type == "pyq":
        if cfg.exam not in ("JEE_MAIN", "JEE_ADVANCED"):
            raise MockTestConfigError("A PYQ mock must be JEE Main or JEE Advanced")
        if (cfg.pyq_length or "full") not in ("full", "short"):
            raise MockTestConfigError("pyq_length must be full or short")
        if cfg.pyq_length == "short" and (not cfg.short_questions or int(cfg.short_questions) < 1):
            raise MockTestConfigError("A shortened PYQ mock needs a question count")
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

        if cfg.mock_type == "pyq":
            if s.n_questions < 0:
                raise MockTestConfigError(f"{s.subject}: n_questions cannot be negative")
        elif s.n_questions < 1:
            raise MockTestConfigError(
                f"{s.subject}: n_questions must be >= 1, got {s.n_questions}"
            )
        if s.n_questions > config.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT:
            raise MockTestConfigError(
                f"{s.subject}: n_questions exceeds maximum "
                f"({config.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT})"
            )
        bad = [t for t in s.question_types if t not in config.QUESTION_TYPE_GROUPS]
        if bad:
            raise MockTestConfigError(
                f"{s.subject}: question_types must be among {sorted(config.QUESTION_TYPE_GROUPS)}, got {bad}"
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


# ── Chapter catalogue and inventory (Postgres is the single source) ───────────
#
# Chapters come from the same place /mock/catalog reads (retrieval store), so every
# chapter_id the picker offers is one the assembler understands. Questions are PYQs that
# have a stored answer key in knowledge_private.solutions; nothing is ever invented.

_ALL_GRADEABLE = tuple(sorted(_GRADEABLE_TYPES))


def normalise_qtype(raw: Optional[str]) -> str:
    """Student-facing question type."""
    low = (raw or "").strip().lower()
    if low in ("numeric", "numerical", "integer", "exercise"):
        return "numeric"
    if low in ("multiple_correct", "multi_correct"):
        return "multiple_correct"
    if low in ("matching", "match_list", "matrix_match"):
        return "matching"
    return "single_correct"


def _chapter_map() -> Dict[str, Dict[str, Any]]:
    """chapter_id -> {chapter_id, title, subject} from the retrieval store."""
    return {
        ch["chapter_id"]: ch
        for ch in store.chapters()
        if ch.get("chapter_id") and ch.get("subject") in VALID_SUBJECTS
    }


def _type_list(allowed: Optional[frozenset]) -> List[str]:
    return sorted(allowed) if allowed else list(_ALL_GRADEABLE)


def _exam_clause(exam: str, params: Dict[str, Any]) -> str:
    exams = config.EXAM_ALLOWED_DB.get(exam, ())
    if not exams:
        return ""
    params["exams"] = list(exams)
    return "AND c.target_exam = ANY(%(exams)s)"


def _inventory(exam: str, allowed: Optional[frozenset] = None) -> Dict[str, int]:
    """chapter_id -> number of gradeable PYQs (answer key present, figure-ready)."""
    params: Dict[str, Any] = {"types": _type_list(allowed)}
    exam_sql = _exam_clause(exam, params)
    rows = store.query(
        f"""
        SELECT c.chapter_id, count(*) AS n
        FROM public.content c
        JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
        WHERE c.content_type = 'PYQ'
          AND lower(c.question_type) = ANY(%(types)s)
          AND sol.answer IS NOT NULL
          AND {store.FIGURE_READY_SQL}
          {exam_sql}
        GROUP BY c.chapter_id
        """,
        params,
    )
    return {r["chapter_id"]: int(r["n"]) for r in rows}


def _allowed_types(subj_cfg: SubjectConfig) -> Optional[frozenset]:
    """Stored question_type values this section may use, or None for no restriction."""
    if not subj_cfg.question_types:
        return None
    return frozenset(s for t in subj_cfg.question_types for s in config.QUESTION_TYPE_GROUPS[t])


def _allocate(n_total: int, n_chapters: int) -> List[int]:
    """Split `n_total` questions across `n_chapters` so the parts sum to exactly `n_total`."""
    if n_chapters <= 0:
        return []
    base, extra = divmod(max(0, n_total), n_chapters)
    return [base + (1 if i < extra else 0) for i in range(n_chapters)]


def _allocate_capped(n_total: int, capacities: List[int]) -> List[int]:
    """Even split that never exceeds a chapter's capacity; overflow moves to chapters with room."""
    out = [min(a, c) for a, c in zip(_allocate(n_total, len(capacities)), capacities)]
    left = min(n_total, sum(capacities)) - sum(out)
    while left > 0:
        moved = False
        for i, cap in enumerate(capacities):
            if left > 0 and out[i] < cap:
                out[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    return out


def _resolve_chapter_ids(
    subj_cfg: SubjectConfig,
    chapter_map: Dict[str, Dict[str, Any]],
    inventory: Dict[str, int],
) -> List[str]:
    """Chapters of this section that actually hold questions.

    Unknown chapter ids and chapters of another subject are rejected loudly: silently
    swapping the student's selection for "everything" made tests unpredictable.
    """
    subject_chapters = [
        cid for cid, ch in chapter_map.items()
        if ch.get("subject") == subj_cfg.subject and inventory.get(cid, 0) > 0
    ]
    if not subj_cfg.chapters:
        return subject_chapters
    from mock_test_agent.syllabus_catalog import corpus_ids_for

    resolved: List[str] = []
    bad: List[str] = []
    for requested in dict.fromkeys(subj_cfg.chapters):
        hits = [c for c in corpus_ids_for(requested) if c in chapter_map]
        if not hits:
            bad.append(requested)
        else:
            resolved.extend(hits)
    if bad:
        raise MockTestConfigError(f"{subj_cfg.subject}: unknown chapter(s) {bad}")
    resolved = list(dict.fromkeys(resolved))
    wrong = [c for c in resolved if chapter_map[c].get("subject") != subj_cfg.subject]
    if wrong:
        raise MockTestConfigError(f"{subj_cfg.subject}: chapter(s) {wrong} belong to another subject")
    return [c for c in resolved if inventory.get(c, 0) > 0]


def check_inventory(cfg: MockTestConfig) -> FeasibilityReport:
    """Questions requested vs available per section. Read-only."""
    chapter_map = _chapter_map()
    total_requested = 0
    total_available = 0
    gaps: List[FeasibilityGap] = []
    warnings: List[str] = []
    cache: Dict[Optional[frozenset], Dict[str, int]] = {}

    for subj_cfg in cfg.subjects:
        allowed = _allowed_types(subj_cfg)
        if allowed not in cache:
            cache[allowed] = _inventory(cfg.exam, allowed)
        inventory = cache[allowed]
        chapter_ids = _resolve_chapter_ids(subj_cfg, chapter_map, inventory)
        total_requested += subj_cfg.n_questions
        if not chapter_ids:
            warnings.append(f"No questions available for {subj_cfg.subject} with these filters")
            continue
        caps = [inventory.get(c, 0) for c in chapter_ids]
        total_available += min(subj_cfg.n_questions, sum(caps))
        for cid, share, cap in zip(chapter_ids, _allocate(subj_cfg.n_questions, len(chapter_ids)), caps):
            if share and cap < share:
                gaps.append(FeasibilityGap(
                    chapter_id=cid,
                    chapter_title=chapter_map.get(cid, {}).get("title", cid),
                    subject=subj_cfg.subject,
                    requested=share,
                    available=cap,
                ))
    return FeasibilityReport(requested=total_requested, available=total_available, gaps=gaps, warnings=warnings)


def build_feasibility_report(cfg: MockTestConfig) -> Dict[str, Any]:
    """Public entry point for the /mock/catalog feasibility check."""
    validate_config(cfg)
    return check_inventory(cfg).to_dict()


# ── Question selection ────────────────────────────────────────────────────────

def _difficulty_for_chapter(subj_cfg: SubjectConfig, exam: str) -> int:
    """Difficulty target: subj override > exam default > 3."""
    if subj_cfg.difficulty_target is not None:
        return subj_cfg.difficulty_target
    return config.MOCK_TEST_DIFFICULTY_DEFAULTS.get(exam, 3)


def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _select_for_chapter(
    *,
    chapter_id: str,
    chapter_map: Dict[str, Dict[str, Any]],
    n_wanted: int,
    exam: str,
    difficulty_target: int,
    exclude_ids: set,
    exclude_fingerprints: set,
    allowed_types: Optional[frozenset] = None,
) -> List[MockTestQuestion]:
    """Up to `n_wanted` gradeable PYQs of one chapter (display_order is set by the caller)."""
    if n_wanted <= 0:
        return []
    ch = chapter_map.get(chapter_id, {})
    params: Dict[str, Any] = {
        "ch": chapter_id,
        "types": _type_list(allowed_types),
        "ex": list(exclude_ids),
        "d": difficulty_target,
        "n": max(n_wanted * 4, n_wanted + 10),
    }
    exam_sql = _exam_clause(exam, params)
    rows = store.query(
        f"""
        SELECT c.content_id, c.primary_concept_id AS concept_id, c.question_type,
               {store.EFFECTIVE_DIFFICULTY_SQL} AS difficulty, c.core_text, c.fingerprint,
               c.structured::text AS structured, sol.answer::text AS answer
        FROM public.content c
        JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
        WHERE c.content_type = 'PYQ'
          AND c.chapter_id = %(ch)s
          AND lower(c.question_type) = ANY(%(types)s)
          AND sol.answer IS NOT NULL
          AND {store.FIGURE_READY_SQL}
          AND NOT (c.content_id = ANY(%(ex)s))
          {exam_sql}
        ORDER BY abs(coalesce({store.EFFECTIVE_DIFFICULTY_SQL}, 3) - %(d)s) + random() * 2
        LIMIT %(n)s
        """,
        params,
    )

    questions: List[MockTestQuestion] = []
    for row in rows:
        if len(questions) >= n_wanted:
            break
        cid = row["content_id"]
        fp = row.get("fingerprint")
        if cid in exclude_ids or (fp and fp in exclude_fingerprints):
            continue
        answer = _as_json(row.get("answer"))
        if not isinstance(answer, dict) or not answer.get("kind"):
            continue
        structured = _as_json(row.get("structured")) or {}
        stem = (structured.get("stem") or row.get("core_text") or "").strip()
        if not stem:
            continue
        options_raw = structured.get("options")
        options: Optional[List[Dict[str, str]]] = None
        if isinstance(options_raw, list) and options_raw:
            options = [{"label": str(o.get("label", "")), "text": str(o.get("text", ""))} for o in options_raw]

        qtype = normalise_qtype(row.get("question_type"))
        # An option-based question without options cannot be answered on screen.
        if qtype != "numeric" and not options:
            continue

        concept_id = row.get("concept_id") or f"con_{chapter_id}"
        concept_row = (store.concept(concept_id) or {}) if row.get("concept_id") else {}
        q = MockTestQuestion(
            content_id=cid,
            display_order=0,
            section=ch.get("subject", ""),
            chapter_id=chapter_id,
            chapter_title=ch.get("title", chapter_id),
            concept_id=concept_id,
            concept_name=concept_row.get("name") or ch.get("title", chapter_id),
            question_type=qtype,
            difficulty=int(row.get("difficulty") or 3),
            stem=stem[:4000],
            options=options if qtype != "numeric" else None,
            has_gradekey=True,
        )
        q._answer_raw = answer  # type: ignore[attr-defined]  private snapshot for the DB insert
        questions.append(q)
        exclude_ids.add(cid)
        if fp:
            exclude_fingerprints.add(fp)
    return questions


def select_questions(
    subj_cfg: SubjectConfig,
    *,
    exam: str,
    exclude_ids: set,
    exclude_fingerprints: set,
    order_start: int = 1,
    allow_fallback: bool = True,
    chapter_map: Optional[Dict[str, Dict[str, Any]]] = None,
    inventory: Optional[Dict[str, int]] = None,
    on_chapter: Optional[Any] = None,
) -> MockTestSection:
    """Build the question list for one subject section.

    Questions are spread over the chosen chapters, capped by what each chapter holds.
    If the section is still short and `allow_fallback` is set, it is topped up from its
    other chapters. `on_chapter(questions)` runs after each chapter's batch so a caller can
    persist progress; display_order is assigned here, consecutively.
    """
    chapter_map = chapter_map if chapter_map is not None else _chapter_map()
    allowed = _allowed_types(subj_cfg)
    inventory = inventory if inventory is not None else _inventory(exam, allowed)
    chapter_ids = _resolve_chapter_ids(subj_cfg, chapter_map, inventory)
    if not chapter_ids:
        return MockTestSection(subject=subj_cfg.subject, chapter_ids=[], questions=[])

    difficulty_target = _difficulty_for_chapter(subj_cfg, exam)
    allocations = _allocate_capped(subj_cfg.n_questions, [inventory.get(c, 0) for c in chapter_ids])
    all_questions: List[MockTestQuestion] = []
    order = order_start

    def take(ch_id: str, want: int) -> None:
        nonlocal order
        qs = _select_for_chapter(
            chapter_id=ch_id, chapter_map=chapter_map, n_wanted=want, exam=exam,
            difficulty_target=difficulty_target, exclude_ids=exclude_ids,
            exclude_fingerprints=exclude_fingerprints, allowed_types=allowed,
        )
        for q in qs:
            q.display_order = order
            order += 1
        all_questions.extend(qs)
        if qs and on_chapter:
            on_chapter(qs)

    for ch_id, alloc in zip(chapter_ids, allocations):
        take(ch_id, alloc)

    if allow_fallback:
        for ch_id in chapter_ids:
            short = subj_cfg.n_questions - len(all_questions)
            if short <= 0:
                break
            take(ch_id, short)

    return MockTestSection(subject=subj_cfg.subject, chapter_ids=chapter_ids, questions=all_questions)


# ── Persistence (Supabase public.mock_* is the only store) ────────────────────
#
# Build progress lives in mock_tests.config["_build"] because the table's status CHECK only
# allows built/in_progress/submitted/graded. While a build runs (or after it fails) the row
# stays 'built' in SQL and get_test() reports the effective status 'building' / 'failed'.

BUILD_STALE_AFTER_SEC = 10 * 60      # a build older than this is reported failed
BUILD_HEARTBEAT_SEC = 4 * 60         # ...or one that has stopped updating (worker died)


class MockStoreError(RuntimeError):
    """The mock-test store (Supabase) could not be reached or written."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _table(name: str):
    return db.client.table(name)


def _question_row(mock_test_id: str, q: MockTestQuestion) -> Dict[str, Any]:
    return {
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
        "answer": getattr(q, "_answer_raw", None),
        "has_gradekey": q.has_gradekey,
    }


class _Build:
    """Live build state for one test; flush() writes it into mock_tests."""

    def __init__(self, mock_test_id: str, cfg: MockTestConfig):
        self.id = mock_test_id
        self.cfg = cfg
        self.state = "building"
        self.stage = "Starting"
        self.error: Optional[str] = None
        self.by_subject = [
            {"subject": s.subject, "prepared": 0, "total": s.n_questions} for s in cfg.subjects
        ]
        self.shortfall: List[Dict[str, Any]] = []

    @property
    def prepared(self) -> int:
        return sum(s["prepared"] for s in self.by_subject)

    def block(self) -> Dict[str, Any]:
        return {
            "state": self.state, "stage": self.stage, "error": self.error,
            "by_subject": self.by_subject, "shortfall": self.shortfall,
            "prepared": self.prepared, "updated_at": _now().isoformat(),
        }

    def flush(self) -> None:
        config_json = dict(self.cfg.to_dict())
        config_json["_build"] = self.block()
        try:
            _table("mock_tests").update(
                {"config": config_json, "total_questions": self.prepared}
            ).eq("mock_test_id", self.id).execute()
        except Exception as exc:
            raise MockStoreError(f"could not save build progress: {exc}") from exc


def _create_building_row(cfg: MockTestConfig) -> _Build:
    mock_test_id = str(uuid.uuid4())
    build = _Build(mock_test_id, cfg)
    config_json = cfg.to_dict()
    config_json["_build"] = build.block()
    try:
        _table("mock_tests").insert({
            "mock_test_id": mock_test_id,
            "user_id": cfg.user_id,
            "exam": cfg.exam,
            "config": config_json,
            "status": "built",     # reported as 'building' via config._build
            "total_questions": 0,
            "questions_requested": cfg.total_questions,
            "time_limit_minutes": cfg.total_time_minutes,
            "subject_scores": {},
            "chapter_scores": {},
        }).execute()
    except Exception as exc:
        raise MockStoreError(f"could not create the mock test: {exc}") from exc
    return build


def _seen_ids(cfg: MockTestConfig) -> set:
    """Questions to treat as already shown. Hard-excluded only when exclude_seen is set."""
    seen: set = set()
    if cfg.exclude_seen:
        try:
            seen.update(store.served_ids(cfg.user_id).keys())
            seen.update(store.served_fingerprints(cfg.user_id))
        except Exception:
            logger.warning("could not load seen questions; continuing without that exclusion", exc_info=True)
    try:
        resp = _table("mock_tests").select("mock_test_id").eq("user_id", cfg.user_id).limit(40).execute()
        rows = resp.data if isinstance(getattr(resp, "data", None), list) else []
        tids = [r["mock_test_id"] for r in rows if isinstance(r, dict) and r.get("mock_test_id")]
        if tids:
            qresp = _table("mock_test_questions").select("content_id").in_("mock_test_id", tids).limit(2000).execute()
            qrows = qresp.data if isinstance(getattr(qresp, "data", None), list) else []
            seen.update(r["content_id"] for r in qrows if isinstance(r, dict) and r.get("content_id"))
    except Exception:
        logger.warning("could not load earlier mock questions", exc_info=True)
    return seen


def _run_build(build: _Build) -> List[MockTestSection]:
    """Select and persist one paper. Smart, Custom, and official PYQ share this path."""
    from mock_test_agent.engine import MockBuildError, compose
    from mock_test_agent.learner import load_states
    from mock_test_agent.pool import fetch

    cfg = build.cfg
    build.stage = "Reading your learning record"
    build.flush()
    learner_rows = load_states(cfg.user_id) if cfg.mock_type != "pyq" else []
    build.stage = "Selecting questions"
    build.flush()
    try:
        rows = fetch(cfg, store_mod=store)
    except Exception as exc:
        raise MockStoreError(f"could not read the question bank: {exc}") from exc
    try:
        paper = compose(cfg, rows, learner_rows, seen=_seen_ids(cfg))
    except MockBuildError as exc:
        raise MockTestConfigError(str(exc)) from exc

    sections: List[MockTestSection] = paper["sections"]
    questions: List[MockTestQuestion] = paper["questions"]
    if not questions:
        raise MockTestConfigError(
            "No questions could be found for this mock. Change the subject, chapters, or exam."
        )

    by_subject = {s.subject: s for s in sections}
    build.by_subject = [
        {"subject": subject, "prepared": 0, "total": len(by_subject.get(subject, MockTestSection(subject, [])).questions)}
        for subject in [s.subject for s in cfg.subjects]
    ]
    for entry in build.by_subject:
        section = by_subject.get(entry["subject"])
        if not section or not section.questions:
            if cfg.mock_type != "smart":
                build.shortfall.append({"subject": entry["subject"], "requested": entry["total"], "got": 0})
            continue
        build.stage = f"Saving {entry['subject']}"
        build.flush()
        try:
            _table("mock_test_questions").insert([_question_row(build.id, q) for q in section.questions]).execute()
        except Exception as exc:
            raise MockStoreError(f"could not save questions: {exc}") from exc
        entry["prepared"] = len(section.questions)
        wanted = next((s.n_questions for s in cfg.subjects if s.subject == entry["subject"]), entry["prepared"])
        if cfg.mock_type != "pyq" and entry["prepared"] < wanted:
            build.shortfall.append({"subject": entry["subject"], "requested": wanted, "got": entry["prepared"]})
        build.flush()

    meta = paper["meta"]
    cfg.marking_scheme = meta.get("marking_scheme") or cfg.marking_scheme
    stored = cfg.to_dict()
    stored.update({k: v for k, v in meta.items() if k != "marking_scheme"})
    stored["marking_scheme"] = cfg.marking_scheme
    stored["_build"] = build.block()
    try:
        _table("mock_tests").update({
            "config": stored,
            "total_questions": len(questions),
            "questions_requested": cfg.total_questions or len(questions),
            "time_limit_minutes": cfg.total_time_minutes,
        }).eq("mock_test_id", build.id).execute()
    except Exception as exc:
        raise MockStoreError(f"could not store the paper: {exc}") from exc
    return sections


def _finish(build: _Build, error: Optional[str] = None) -> None:
    build.state = "failed" if error else "built"
    build.stage = "Build failed" if error else "Ready"
    build.error = error
    try:
        build.flush()
    except MockStoreError:
        logger.error("could not record final build state for %s", build.id, exc_info=True)


def _background_worker(build: _Build) -> None:
    try:
        _run_build(build)
        _finish(build)
        logger.info("mock test %s built: %d questions", build.id, build.prepared)
    except Exception as exc:
        logger.error("mock test %s build failed", build.id, exc_info=True)
        _finish(build, error=str(exc) or exc.__class__.__name__)


def start_background_assembly(cfg: MockTestConfig) -> Dict[str, Any]:
    """Validate, create the row as 'building', start a worker thread, return immediately."""
    validate_config(cfg)
    build = _create_building_row(cfg)
    threading.Thread(
        target=_background_worker, args=(build,), name=f"mock-build-{build.id[:8]}", daemon=True
    ).start()
    return {
        "mock_test_id": build.id,
        "status": "building",
        "total_questions": cfg.total_questions,
        "time_limit_minutes": cfg.total_time_minutes,
    }


def assemble_test(cfg: MockTestConfig) -> Dict[str, Any]:
    """Synchronous build: same pipeline, returns the sections and feasibility report.

    Raises MockTestConfigError for invalid configs or when no question could be found.
    """
    validate_config(cfg)
    build = _create_building_row(cfg)
    try:
        sections = _run_build(build)
    except Exception as exc:
        _finish(build, error=str(exc) or exc.__class__.__name__)
        raise
    _finish(build)
    return {
        "mock_test_id": build.id,
        "user_id": cfg.user_id,
        "exam": cfg.exam,
        "status": "built",
        "total_questions": build.prepared,
        "time_limit_minutes": cfg.total_time_minutes,
        "sections": [s.to_dict() for s in sections],
        "feasibility": {
            "requested": cfg.total_questions,
            "available": build.prepared,
            "shortfall": build.shortfall,
        },
        "shortfall": build.shortfall,
    }


# ── Read helpers (used by API routes) ────────────────────────────────────────

def _effective(row: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay the build state on the SQL status; mark abandoned builds failed."""
    cfg_json = row.get("config")
    b = cfg_json.get("_build") if isinstance(cfg_json, dict) else None
    if row.get("status") == "built" and isinstance(b, dict) and b.get("state") in ("building", "failed"):
        state, error = b["state"], b.get("error")
        if state == "building":
            created = _parse_ts(row.get("created_at"))
            beat = _parse_ts(b.get("updated_at")) or created
            now = _now()
            if (created and (now - created).total_seconds() > BUILD_STALE_AFTER_SEC) or (
                beat and (now - beat).total_seconds() > BUILD_HEARTBEAT_SEC
            ):
                state, error = "failed", "The test build timed out. Please build it again."
                try:
                    new_cfg = dict(cfg_json)
                    new_cfg["_build"] = {**b, "state": "failed", "stage": "Build failed", "error": error}
                    _table("mock_tests").update({"config": new_cfg}).eq(
                        "mock_test_id", row["mock_test_id"]
                    ).eq("status", "built").execute()
                except Exception:
                    logger.warning("could not persist stale-build failure", exc_info=True)
        row = {**row, "status": state, "_build_error": error}
    return row


def get_test(mock_test_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """The mock_tests row with its effective status, or None if absent / not the caller's."""
    try:
        resp = (
            _table("mock_tests").select("*")
            .eq("mock_test_id", mock_test_id).eq("user_id", user_id).limit(1).execute()
        )
    except Exception as exc:
        # A malformed uuid is "not found"; anything else is a store outage.
        if "invalid input syntax" in str(exc).lower():
            return None
        raise MockStoreError(f"could not read the mock test: {exc}") from exc
    rows = resp.data or []
    return _effective(rows[0]) if rows else None


def get_test_questions(mock_test_id: str, include_answers: bool = False) -> List[Dict[str, Any]]:
    """All questions of a test ordered by display_order; `answer` is stripped unless asked for.

    question_type is always one of single_correct | multiple_correct | numeric.
    """
    try:
        resp = (
            _table("mock_test_questions").select("*")
            .eq("mock_test_id", mock_test_id).order("display_order").limit(1000).execute()
        )
    except Exception as exc:
        raise MockStoreError(f"could not read the questions: {exc}") from exc
    rows = resp.data or []
    for r in rows:
        r["question_type"] = normalise_qtype(r.get("question_type"))
        if not include_answers:
            r.pop("answer", None)
    return rows


def get_submissions(mock_test_id: str, user_id: str) -> List[Dict[str, Any]]:
    try:
        resp = (
            _table("mock_test_submissions").select("*")
            .eq("mock_test_id", mock_test_id).eq("user_id", user_id).limit(1000).execute()
        )
    except Exception as exc:
        raise MockStoreError(f"could not read the answers: {exc}") from exc
    return resp.data or []


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
        _table("mock_test_submissions").upsert(row, on_conflict="mock_test_id,content_id,user_id").execute()
    except Exception as exc:
        raise MockStoreError(f"could not save the answer: {exc}") from exc
    return {"recorded": True}


def build_status(test: Dict[str, Any]) -> Dict[str, Any]:
    """The GET /status payload for an (effective-status) mock_tests row."""
    status = test["status"]
    cfg_json = test.get("config") if isinstance(test.get("config"), dict) else {}
    b = cfg_json.get("_build") or {}
    total = int(test.get("questions_requested") or 0)
    if b:
        by_subject = b.get("by_subject") or []
        prepared = sum(int(s.get("prepared") or 0) for s in by_subject)
    else:  # legacy row without build info: count what is stored
        qs = get_test_questions(test["mock_test_id"])
        counts: Dict[str, int] = {}
        for q in qs:
            counts[q.get("section")] = counts.get(q.get("section"), 0) + 1
        by_subject = [
            {"subject": s.get("subject"), "prepared": counts.get(s.get("subject"), 0),
             "total": s.get("n_questions", 0)}
            for s in cfg_json.get("subjects", [])
        ]
        prepared = len(qs)
    stage = {
        "building": b.get("stage") or "Building",
        "failed": "Build failed",
        "built": "Ready",
        "in_progress": "In progress",
        "submitted": "Submitted",
        "graded": "Graded",
    }.get(status, status)
    return {
        "mock_test_id": test["mock_test_id"],
        "status": status,
        "stage": stage,
        "prepared": prepared,
        "total": total,
        "by_subject": by_subject,
        "error": (test.get("_build_error") or b.get("error")) if status == "failed" else None,
        "shortfall": b.get("shortfall") or [],
    }


def list_tests(user_id: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """The caller's tests, newest first, with score summary (null score when ungraded)."""
    try:
        resp = (
            _table("mock_tests")
            .select(
                "mock_test_id,exam,status,config,total_questions,questions_requested,created_at,"
                "started_at,submitted_at,raw_score,max_score,accuracy_pct,subject_scores",
                count="exact",
            )
            .eq("user_id", user_id).order("created_at", desc=True)
            .range(offset, offset + limit - 1).execute()
        )
    except Exception as exc:
        raise MockStoreError(f"could not read the test history: {exc}") from exc

    tests: List[Dict[str, Any]] = []
    for raw in resp.data or []:
        row = _effective(raw)
        status = row["status"]
        cfg_json = row.get("config") if isinstance(row.get("config"), dict) else {}
        graded = status == "graded"
        subj_scores = row.get("subject_scores") or {}
        attempted: Optional[int] = None
        correct: Optional[int] = None
        if graded and isinstance(subj_scores, dict):
            attempted = sum(int(v.get("questions_attempted") or 0) for v in subj_scores.values())
            correct = sum(int(v.get("questions_correct") or 0) for v in subj_scores.values())
        elif status in ("in_progress", "submitted"):
            try:
                subs = get_submissions(row["mock_test_id"], user_id)
                attempted = sum(1 for s in subs if _has_answer(s))
            except MockStoreError:
                attempted = None
        tests.append({
            "mock_test_id": row["mock_test_id"],
            "exam": row.get("exam"),
            "status": status,
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "submitted_at": row.get("submitted_at"),
            "total_questions": row.get("total_questions") or 0,
            "attempted": attempted,
            "correct": correct,
            "score": row.get("raw_score") if graded else None,
            "max_score": row.get("max_score") if graded else None,
            "accuracy_pct": row.get("accuracy_pct") if graded else None,
            "subjects": [s.get("subject") for s in cfg_json.get("subjects", []) if s.get("subject")],
        })
    return {"tests": tests, "total": int(resp.count if resp.count is not None else len(tests))}


def _has_answer(sub: Dict[str, Any]) -> bool:
    return bool(
        sub.get("choice")
        or (sub.get("student_answer") or "").strip()
        or sub.get("numeric_value") is not None
    )
