"""Mock generation for Smart, Custom, and official PYQ papers.

Selection and scoring stay deterministic. The learner index and the blueprint
decide the paper; a model is not asked to invent questions or answers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from mock_test_agent import settings as config
from mock_test_agent.blueprints import (
    SUBJECT_ORDER,
    blueprint_for,
    public_blueprint,
    sectional_label,
)
from mock_test_agent.learner import fingerprint, learned_index
from mock_test_agent.models import MockTestConfig, MockTestQuestion, MockTestSection
from mock_test_agent.pool import paper_key_for
from mock_test_agent.selector import allocate_and_pick, even_shares

FORMAT_GROUP = {
    "single_correct": "single",
    "comprehension": "single",
    "multiple_correct": "multiple",
    "multi_correct": "multiple",
    "numeric": "numerical",
    "numerical": "numerical",
    "integer": "numerical",
}


class MockBuildError(ValueError):
    """The paper cannot be built from the questions and learner state we have."""


def _exam_target(cfg: MockTestConfig) -> int:
    explicit = [s.difficulty_target for s in cfg.subjects if s.difficulty_target]
    if explicit:
        return int(explicit[0])
    return int(config.MOCK_TEST_DIFFICULTY_DEFAULTS.get(cfg.exam, 3))


def _wanted(cfg: MockTestConfig) -> Dict[str, int]:
    return {s.subject: max(0, int(s.n_questions)) for s in cfg.subjects}


def _type_allowed(cfg: MockTestConfig, subject: str, qtype: str) -> bool:
    sub = cfg.subject_config(subject)
    if not sub or not sub.question_types:
        return True
    return FORMAT_GROUP.get(qtype, "") in set(sub.question_types)


def _filter_rows(cfg: MockTestConfig, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    subjects = set(cfg.subject_names)
    chapters = {c for s in cfg.subjects for c in (s.chapters or [])}
    out = []
    for row in rows:
        if row.get("subject") not in subjects:
            continue
        if cfg.mock_type != "smart" and cfg.mock_type != "pyq" and chapters:
            if row.get("chapter_id") not in chapters:
                continue
        if not _type_allowed(cfg, row["subject"], str(row.get("question_type") or "")):
            continue
        if cfg.mock_type == "pyq":
            key = paper_key_for(str(row.get("source_book") or ""), str(row.get("license") or ""))
            if not key:
                continue
            wanted = getattr(cfg, "paper_key", None)
            if wanted:
                if key != wanted:
                    continue
            elif cfg.year and cfg.exam == "JEE_MAIN":
                if not key.startswith("main:") or f"_{int(cfg.year)}_" not in key:
                    continue
            elif cfg.year:
                exact = f"advanced:{int(cfg.year)}:{int(cfg.paper)}" if cfg.paper else f"advanced:{int(cfg.year)}:"
                if cfg.paper and key != exact:
                    continue
                if not cfg.paper and not key.startswith(exact):
                    continue
        out.append(row)
    return out


def _assign_sections(
    questions: List[Dict[str, Any]],
    blueprint: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Order questions by subject, then by blueprint section. A question keeps its format."""
    slots = list(blueprint["sections"])
    by_subject: Dict[str, List[Dict[str, Any]]] = {}
    for q in questions:
        by_subject.setdefault(q["subject"], []).append(q)
    ordered: List[Dict[str, Any]] = []
    for subject in SUBJECT_ORDER:
        group = by_subject.get(subject) or []
        if not group:
            continue
        used: Set[str] = set()
        for slot in slots:
            formats = set(slot["formats"])
            for q in group:
                if q["content_id"] in used or q.get("question_type") not in formats:
                    continue
                q["paper_section"] = f"{subject} · {slot['title']}"
                q["instructions"] = slot["instructions"]
                q["marks"] = dict(slot["marks"])
                ordered.append(q)
                used.add(q["content_id"])
        for q in group:
            if q["content_id"] in used:
                continue
            q["paper_section"] = f"{subject} · Ungraded format"
            q["instructions"] = "This question's format is not part of the selected blueprint, so it is not included."
            # Leave it out. An unmatched format is not silently scored as something else.
        if any(q["content_id"] not in used for q in group):
            pass
    return ordered


def _to_question(row: Dict[str, Any], order: int) -> MockTestQuestion:
    qtype = str(row.get("question_type") or "")
    if qtype == "numerical":
        qtype = "numeric"
    if qtype == "multi_correct":
        qtype = "multiple_correct"
    options = row.get("options")
    q = MockTestQuestion(
        content_id=row["content_id"],
        display_order=order,
        section=row.get("subject") or "",
        chapter_id=row.get("chapter_id") or "",
        chapter_title=row.get("chapter_title") or row.get("chapter_id") or "",
        concept_id=row.get("concept_id") or "",
        concept_name=row.get("concept_name") or row.get("concept_id") or "",
        question_type=qtype,
        difficulty=int(row.get("difficulty") or 3),
        stem=row.get("stem") or "",
        options=None if qtype == "numeric" else options,
        has_gradekey=True,
        paper_section=row.get("paper_section") or row.get("subject") or "",
        instructions=row.get("instructions") or "",
        source_type=row.get("content_type") or "",
        previously_seen=bool(row.get("previously_seen")),
    )
    q._answer_raw = row.get("answer")  # type: ignore[attr-defined]
    q._marks = row.get("marks")  # type: ignore[attr-defined]
    return q


def compose(
    cfg: MockTestConfig,
    rows: Sequence[Dict[str, Any]],
    learner_rows: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    seen: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Build the immutable question list for one paper. Raises MockBuildError when it cannot."""
    if cfg.mock_type == "pyq":
        from mock_test_agent.pyq_select import PyqBuildError, select_pyq
        try:
            return select_pyq(cfg, rows, seen=seen)
        except PyqBuildError as exc:
            raise MockBuildError(str(exc)) from exc
    seen = set(seen or ())
    pool = _filter_rows(cfg, rows)
    # Drop duplicates and, when asked, anything the student was already served.
    deduped: List[Dict[str, Any]] = []
    fingerprints: Set[str] = set()
    for row in pool:
        cid = row.get("content_id")
        fp = row.get("fingerprint") or ""
        if not cid or cid in {q["content_id"] for q in deduped}:
            continue
        if fp and fp in fingerprints:
            continue
        if cfg.exclude_seen and (cid in seen or (fp and fp in seen)):
            continue
        deduped.append(dict(row))
        if fp:
            fingerprints.add(fp)

    learned = learned_index(list(learner_rows or []))
    subjects_filter = set(cfg.subject_names)
    if cfg.mock_type == "smart":
        learned = {cid: info for cid, info in learned.items() if not info["subject"] or info["subject"] in subjects_filter}
        if not learned:
            raise MockBuildError(
                "Smart Mock needs concepts you have already practised. "
                "Study a few concepts in the Study Room, then come back. "
                "Nothing you have not learned is treated as a weakness."
            )

    blueprint = blueprint_for(cfg.exam)
    exam_target = _exam_target(cfg)
    seed = f"{cfg.user_id}|{cfg.mock_type}|{cfg.exam}|{fingerprint(learned)}|{cfg.year}|{cfg.paper}"

    personalised = True
    coverage = "learned" if cfg.mock_type == "smart" or cfg.coverage == "learned" else "all"
    wanted = _wanted(cfg)
    # Smart spreads one total across subjects that have learned concepts.
    if cfg.mock_type == "smart":
        # A selected subject is included whenever the bank has questions for it.
        # Learned concepts change the mix inside that subject. They do not remove it.
        present_subjects = [
            subject for subject in cfg.subject_names
            if any(q.get("subject") == subject for q in deduped)
        ]
        if not present_subjects:
            raise MockBuildError(
                "None of the selected subjects have a gradeable question in the bank."
            )
        total = sum(wanted.values()) or min(20, len(deduped))
        shares = even_shares(total, present_subjects)
    else:
        shares = {s: n for s, n in wanted.items() if n > 0}
        if not shares:
            raise MockBuildError("Choose at least one question.")

    picked = []
    allocation = {}
    contains_unlearned = False
    for subject, n in shares.items():
        subject_rows = [q for q in deduped if q.get("subject") == subject]
        subject_learned = {
            cid: info for cid, info in learned.items()
            if info["subject"] in ("", subject)
        }
        # Custom "entire chapter" still personalises inside the chapter boundary.
        # Give each chosen chapter a share so one weak concept cannot eat the paper.
        if cfg.mock_type == "custom":
            chapters = cfg.subject_config(subject).chapters if cfg.subject_config(subject) else []
            chapter_keys = chapters or sorted({q.get("chapter_id") or "" for q in subject_rows if q.get("chapter_id")})
            chapter_share = even_shares(n, chapter_keys) if chapter_keys else {"": n}
            subject_picked: List[Dict[str, Any]] = []
            for chapter_id, count in chapter_share.items():
                chunk = [q for q in subject_rows if not chapter_id or q.get("chapter_id") == chapter_id]
                part = allocate_and_pick(
                    chunk, subject_learned, count,
                    coverage=coverage, exam_target=exam_target, seen=seen, seed=seed,
                )
                subject_picked.extend(part["questions"])
                contains_unlearned = contains_unlearned or part["contains_unlearned"]
                for band, k in part["allocation"].items():
                    allocation[band] = allocation.get(band, 0) + k
            picked.extend(subject_picked)
        else:
            part = allocate_and_pick(
                subject_rows, subject_learned, n,
                coverage="learned" if subject_learned else "all",
                exam_target=exam_target, seen=seen, seed=seed,
            )
            contains_unlearned = contains_unlearned or part["contains_unlearned"]
            if not part["questions"]:
                continue
            picked.extend(part["questions"])
            for band, k in part["allocation"].items():
                allocation[band] = allocation.get(band, 0) + k
    if not picked:
        raise MockBuildError(
            "Not enough gradeable questions for this selection. "
            "Add a chapter, include concepts you have learned, or choose another subject."
        )

    ordered = _assign_sections(picked, blueprint)
    questions = [_to_question(row, i) for i, row in enumerate(ordered, start=1)]
    by_subject: Dict[str, List[MockTestQuestion]] = {}
    for q in questions:
        by_subject.setdefault(q.section, []).append(q)
    sections = [
        MockTestSection(subject=subject, chapter_ids=sorted({q.chapter_id for q in qs if q.chapter_id}), questions=qs)
        for subject, qs in by_subject.items()
    ]
    per_subject_counts = [len(s.questions) for s in sections]
    typical = per_subject_counts[0] if per_subject_counts else 0
    sectional = sectional_label(
        [s.subject for s in sections], blueprint, typical if len(set(per_subject_counts)) == 1 else 0,
    )
    scheme: Dict[str, Dict[str, Any]] = {}
    for slot in blueprint["sections"]:
        for fmt in slot["formats"]:
            if fmt == "comprehension":
                scheme.setdefault("comprehension", dict(slot["marks"]))
            elif fmt in ("numeric", "integer", "numerical"):
                scheme.setdefault("numeric", dict(slot["marks"]))
                scheme.setdefault("integer", dict(slot["marks"]))
            elif fmt == "multiple_correct":
                scheme.setdefault("multiple_correct", dict(slot["marks"]))
            else:
                scheme.setdefault("single_correct", dict(slot["marks"]))

    meta = {
        "mock_type": cfg.mock_type,
        "coverage": "learned" if cfg.mock_type == "smart" else cfg.coverage,
        "blueprint_id": blueprint["id"],
        "blueprint_version": blueprint["version"],
        "blueprint": public_blueprint(blueprint),
        "sectional": sectional,
        "contains_unlearned": contains_unlearned,
        "allocation": allocation,
        "learned_concepts": sorted(learned),
        "learner_fingerprint": fingerprint(learned),
        "year": cfg.year,
        "paper": cfg.paper,
        "organizing_iit": None,
        "personalised": personalised,
        "marking_scheme": scheme,
        "question_meta": {
            q.content_id: {
                "paper_section": q.paper_section,
                "instructions": q.instructions,
                "source_type": q.source_type,
                "previously_seen": q.previously_seen,
                "marks": getattr(q, "_marks", None),
            }
            for q in questions
        },
    }
    return {"sections": sections, "questions": questions, "meta": meta, "blueprint": blueprint}


def home_summary(
    *,
    learner_rows: Sequence[Dict[str, Any]],
    subject_counts: Dict[str, Dict[str, int]],
    exam: str,
) -> Dict[str, Any]:
    """What the mock home can say before a paper is assembled."""
    learned = learned_index(list(learner_rows))
    bands = {"weak": 0, "medium": 0, "strong": 0}
    by_subject: Dict[str, int] = {}
    for info in learned.values():
        bands[info["band"]] += 1
        if info["subject"]:
            by_subject[info["subject"]] = by_subject.get(info["subject"], 0) + 1
    blueprint = blueprint_for(exam)
    available_subjects = [
        subject for subject in SUBJECT_ORDER
        if int((subject_counts.get(subject) or {}).get("gradeable") or 0) > 0
    ]
    smart_subjects = [
        subject for subject in available_subjects
        if by_subject.get(subject, 0) > 0 or any(not info["subject"] for info in learned.values())
    ]
    ready = bool(learned) and bool(smart_subjects)
    recommended = 0
    if ready:
        recommended = min(20, sum(int((subject_counts.get(s) or {}).get("gradeable") or 0) for s in smart_subjects))
        recommended = max(recommended, 0)
    return {
        "exam": exam,
        "blueprint": public_blueprint(blueprint),
        "learner_fingerprint": fingerprint(learned),
        "learned_concepts": len(learned),
        "bands": bands,
        "learned_by_subject": by_subject,
        "subjects": [
            {
                "subject": subject,
                "gradeable": int((subject_counts.get(subject) or {}).get("gradeable") or 0),
                "official_pyqs": int((subject_counts.get(subject) or {}).get("official_pyqs") or 0),
                "learned_concepts": by_subject.get(subject, 0),
                "supported": subject in available_subjects,
            }
            for subject in SUBJECT_ORDER
        ],
        "smart": {
            "ready": ready,
            "message": (
                "Your next personalized mock is ready."
                if ready else
                "Smart Mock starts after you practise at least one concept. Unlearned concepts are not filled in as weaknesses."
            ),
            "recommended_questions": recommended if ready else 0,
            "recommended_minutes": 45 if recommended and recommended < blueprint["full_per_subject"] * 3 else blueprint["full_duration_minutes"],
            "subjects": smart_subjects,
        },
        "pyq_limitation": (
            "JEE PYQ Mock builds one verified blueprint, then fills it with genuine previous-year questions "
            "from the years stored for that exam. It does not reproduce a single historical paper."
        ),
    }
