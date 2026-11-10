"""Blueprint-first selection of genuine mixed-year PYQs.

The examination structure is chosen before any question is. Each section is then
filled from verified previous-year questions that match the subject, the
student's chapters and concepts, and that section's question formats. A full
mock is rejected when a section cannot be filled. It is never shortened in
secret.
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional, Sequence, Set

from mock_test_agent.blueprints import (
    SUBJECT_ORDER,
    duration_minutes,
    full_question_count,
    public_blueprint,
    pyq_blueprint,
    section_plan,
)
from mock_test_agent.models import MockTestConfig, MockTestQuestion, MockTestSection
from mock_test_agent.pool import question_number, source_of

# A question is in scope when its primary concept (and chapter) was selected.
# Secondary tags never pull an out-of-syllabus question in, and they do not
# push out a question whose primary concept was selected.
SYLLABUS_POLICY = "primary_concept_in_selected_chapters"

# Many Advanced matching lists are stored as single-correct questions: the student
# matches List-I to List-II by choosing an option. They still belong in the matching section.
_MATCH_LIST = re.compile(
    r"list\s*[-–—]\s*i\b|matching\s+list|match\s+the\s+list|match\s+list\s*[-–—]\s*i\b",
    re.I,
)

_FORMAT_GROUP = {
    "single_correct": "single_correct",
    "comprehension": "single_correct",
    "multiple_correct": "multiple_correct",
    "multi_correct": "multiple_correct",
    "numeric": "numeric",
    "numerical": "numeric",
    "integer": "integer",
    "matching": "matching",
    "match_list": "matching",
    "matrix_match": "matching",
}


class PyqBuildError(ValueError):
    """The requested blueprint cannot be filled from genuine PYQs."""


def _group(qtype: str) -> str:
    return _FORMAT_GROUP.get((qtype or "").strip().lower(), "")


def _is_matching_list(row: Dict[str, Any]) -> bool:
    """True when this PYQ is a matching list, including ones saved as an MCQ."""
    if _group(str(row.get("question_type") or "")) == "matching":
        return True
    text = f"{row.get('heading') or ''}\n{row.get('stem') or ''}"
    return _MATCH_LIST.search(text) is not None


def _stem_key(stem: str) -> str:
    return re.sub(r"\s+", " ", (stem or "").lower()).strip()[:240]


def _in_syllabus(cfg: MockTestConfig, row: Dict[str, Any]) -> bool:
    sub = cfg.subject_config(row.get("subject") or "")
    if sub is None:
        return False
    chapter = row.get("chapter_id") or ""
    if sub.chapters and chapter not in set(sub.chapters):
        return False
    concept = row.get("concept_id") or ""
    if sub.concepts and concept not in set(sub.concepts):
        return False
    return True


def _usable(row: Dict[str, Any], source: Dict[str, Any]) -> bool:
    answer = row.get("answer")
    if not isinstance(answer, dict) or not answer.get("kind"):
        return False
    if not (row.get("stem") or "").strip():
        return False
    qtype = _group(str(row.get("question_type") or ""))
    if not qtype:
        return False
    options = row.get("options")
    if qtype in ("numeric", "integer"):
        return True
    return isinstance(options, list) and len(options) >= 2


def eligible_rows(cfg: MockTestConfig, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Genuine PYQs for this exam and syllabus, with duplicate copies removed."""
    kept: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    seen_fp: Set[str] = set()
    seen_origin: Set[str] = set()
    seen_stem: Set[str] = set()
    for raw in rows:
        if (raw.get("content_type") or "") != "PYQ":
            continue
        if raw.get("subject") not in set(cfg.subject_names):
            continue
        source = source_of(str(raw.get("source_book") or ""), str(raw.get("license") or ""))
        if not source:
            continue
        if cfg.exam == "JEE_MAIN" and source["exam"] != "JEE_MAIN":
            continue
        if cfg.exam == "JEE_ADVANCED" and source["exam"] not in ("JEE_ADVANCED", "IIT_JEE"):
            continue
        if not _in_syllabus(cfg, raw):
            continue
        if not _usable(raw, source):
            continue
        cid = raw.get("content_id")
        if not cid or cid in seen_ids:
            continue
        fp = raw.get("fingerprint") or ""
        if fp and fp in seen_fp:
            continue
        number = question_number(raw)
        origin = ""
        if number < 10_000:
            origin = f"{source['key']}|{raw.get('subject')}|{number}"
            if origin in seen_origin:
                continue
        stem = _stem_key(raw.get("stem") or "")
        stem_id = f"{raw.get('subject')}|{stem}"
        if stem and stem_id in seen_stem:
            continue
        item = dict(raw)
        item["source_exam"] = source["exam"]
        item["source_year"] = source["year"]
        item["source_paper"] = source.get("paper")
        item["source_label"] = source["label"]
        item["source_key"] = source["key"]
        item["question_type"] = _group(str(raw.get("question_type") or ""))
        kept.append(item)
        seen_ids.add(cid)
        if fp:
            seen_fp.add(fp)
        if origin:
            seen_origin.add(origin)
        if stem:
            seen_stem.add(stem_id)
    return kept


def _subject_targets(cfg: MockTestConfig, blueprint: Dict[str, Any]) -> Dict[str, int]:
    subjects = [s for s in SUBJECT_ORDER if s in set(cfg.subject_names)]
    full = int(blueprint["full_per_subject"])
    if cfg.pyq_length != "short":
        return {subject: full for subject in subjects}
    total = int(cfg.short_questions or 0)
    if total <= 0:
        total = max(len(subjects), (full * len(subjects)) // 2)
    base, extra = divmod(total, len(subjects))
    return {subject: base + (1 if i < extra else 0) for i, subject in enumerate(subjects)}


def _slots(cfg: MockTestConfig, blueprint: Dict[str, Any]) -> List[Dict[str, Any]]:
    targets = _subject_targets(cfg, blueprint)
    slots: List[Dict[str, Any]] = []
    for subject in SUBJECT_ORDER:
        if subject not in targets:
            continue
        for section in section_plan(blueprint, targets[subject]):
            slots.append({
                "subject": subject,
                "section_id": section["id"],
                "title": section["title"],
                "formats": set(section["formats"]),
                "count": int(section["count"]),
                "marks": dict(section["marks"]),
                "instructions": section["instructions"],
            })
    return slots


def _take(
    pool: List[Dict[str, Any]],
    count: int,
    year_use: Dict[Any, int],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """Round-robin across chapters, preferring years not yet used much."""
    if count <= 0 or not pool:
        return []
    by_chapter: Dict[str, List[Dict[str, Any]]] = {}
    for row in pool:
        jitter = rng.random()
        by_chapter.setdefault(row.get("chapter_id") or "", []).append((year_use.get(row.get("source_year"), 0), jitter, row))
    chapters = list(by_chapter)
    rng.shuffle(chapters)
    for rows in by_chapter.values():
        rows.sort(key=lambda item: (item[0], item[1]))
    chosen: List[Dict[str, Any]] = []
    index = {chapter: 0 for chapter in chapters}
    while len(chosen) < count and any(index[ch] < len(by_chapter[ch]) for ch in chapters):
        for chapter in chapters:
            if index[chapter] >= len(by_chapter[chapter]):
                continue
            row = by_chapter[chapter][index[chapter]][2]
            index[chapter] += 1
            chosen.append(row)
            year = row.get("source_year")
            year_use[year] = year_use.get(year, 0) + 1
            if len(chosen) >= count:
                break
    return chosen


def _pick_slot(
    candidates: List[Dict[str, Any]],
    count: int,
    seen: Set[str],
    year_use: Dict[Any, int],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    fresh = [
        row for row in candidates
        if row["content_id"] not in seen and (row.get("fingerprint") or "") not in seen
    ]
    fresh_ids = {row["content_id"] for row in fresh}
    stale = [row for row in candidates if row["content_id"] not in fresh_ids]
    chosen = _take(fresh, count, year_use, rng)
    if len(chosen) < count:
        chosen.extend(_take(stale, count - len(chosen), year_use, rng))
    return chosen


def _shortage_message(length: str, shortages: List[Dict[str, Any]], eligible: int) -> str:
    detail = "; ".join(
        f"{item['subject']} {item['title']}: {item['available']} available, {item['needed']} required"
        for item in shortages
    )
    if length == "full":
        return (
            f"Only {eligible} valid PYQs are available for the selected chapters and required question types. "
            "A full mock cannot be generated. "
            f"{detail}. "
            "You can include additional chapters or select a shortened mock."
        )
    return (
        f"Only {eligible} valid PYQs match the selected chapters and this shortened blueprint. "
        f"{detail}. "
        "Include additional chapters or lower the question count."
    )


def _to_question(row: Dict[str, Any], order: int) -> MockTestQuestion:
    qtype = str(row.get("question_type") or "")
    if qtype == "numerical":
        qtype = "numeric"
    if qtype == "multi_correct":
        qtype = "multiple_correct"
    options = None if qtype in ("numeric", "integer") else row.get("options")
    question = MockTestQuestion(
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
        options=options,
        has_gradekey=True,
        paper_section=row.get("paper_section") or "",
        instructions=row.get("instructions") or "",
        source_type="PYQ",
        previously_seen=bool(row.get("previously_seen")),
    )
    question._answer_raw = row.get("answer")  # type: ignore[attr-defined]
    question._marks = row.get("marks")  # type: ignore[attr-defined]
    question._source_year = row.get("source_year")  # type: ignore[attr-defined]
    question._source_label = row.get("source_label")  # type: ignore[attr-defined]
    question._source_exam = row.get("source_exam")  # type: ignore[attr-defined]
    question._section_label = row.get("section_label")  # type: ignore[attr-defined]
    question._solution = (row.get("solution") or "")[:6000]  # type: ignore[attr-defined]
    question._matching_list = bool(row.get("matching_list"))  # type: ignore[attr-defined]
    return question


def _label(cfg: MockTestConfig, blueprint: Dict[str, Any], n_subjects: int) -> Dict[str, Any]:
    exam_name = "JEE Main" if cfg.exam == "JEE_MAIN" else "JEE Advanced"
    sectional = n_subjects < 3
    shortened = cfg.pyq_length == "short"
    if shortened and sectional:
        text = f"Shortened {exam_name} sectional simulation"
    elif shortened:
        text = f"Shortened {exam_name} simulation"
    elif sectional:
        text = f"{exam_name} sectional mock"
    else:
        text = f"Full {exam_name}-style mock"
    official = not shortened and not sectional
    return {
        "length_label": text,
        "sectional": not official,
        "official_complete": official,
        "display_title": f"AerOpt {exam_name} PYQ Mock",
        "disclaimer": (
            f"Every question is a genuine previous-year question. "
            f"The paper follows the {blueprint['title']} blueprint, including its section counts and marking. "
            "Questions may come from more than one year. "
            + (
                "This is a shortened simulation, not a complete official paper."
                if shortened else
                "This is a subject-sectional mock, not a complete official examination."
                if sectional else
                "This is a full-length simulation of that blueprint, not a reproduction of one historical paper."
            )
        ),
    }


def _validate(questions: List[MockTestQuestion], slots: List[Dict[str, Any]], cfg: MockTestConfig) -> None:
    if len(questions) != sum(slot["count"] for slot in slots):
        raise PyqBuildError("The generated paper failed validation and was rejected. The question count does not match the blueprint.")
    ids = [q.content_id for q in questions]
    if len(ids) != len(set(ids)):
        raise PyqBuildError("The generated paper failed validation and was rejected. It contained a duplicate question.")
    cursor = 0
    for slot in slots:
        chunk = questions[cursor:cursor + slot["count"]]
        cursor += slot["count"]
        if len(chunk) != slot["count"]:
            raise PyqBuildError("The generated paper failed validation and was rejected. A section is incomplete.")
        for question in chunk:
            if question.section != slot["subject"]:
                raise PyqBuildError("The generated paper failed validation and was rejected. A question is in the wrong subject.")
            if _group(question.question_type) not in {_group(fmt) for fmt in slot["formats"]}:
                listed = bool(getattr(question, "_matching_list", False))
                if not (slot["section_id"] == "matching" and listed and _group(question.question_type) in {"single_correct", "multiple_correct"}):
                    raise PyqBuildError("The generated paper failed validation and was rejected. A question has the wrong format for its section.")
            if not getattr(question, "_answer_raw", None):
                raise PyqBuildError("The generated paper failed validation and was rejected. A question has no answer key.")
            sub = cfg.subject_config(question.section)
            if sub and sub.chapters and question.chapter_id not in set(sub.chapters):
                raise PyqBuildError("The generated paper failed validation and was rejected. A question is outside the selected chapters.")
            if sub and sub.concepts and question.concept_id not in set(sub.concepts):
                raise PyqBuildError("The generated paper failed validation and was rejected. A question is outside the selected concepts.")
            if question.paper_section != f"{slot['subject']} · {slot['title']}":
                raise PyqBuildError("The generated paper failed validation and was rejected. Section labels do not match the blueprint.")


def select_pyq(
    cfg: MockTestConfig,
    rows: Sequence[Dict[str, Any]],
    *,
    seen: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Build one immutable PYQ paper, or raise PyqBuildError with the shortage."""
    try:
        blueprint = pyq_blueprint(cfg.exam, cfg.blueprint_id)
    except KeyError as exc:
        raise PyqBuildError(str(exc)) from exc

    subjects = [s for s in SUBJECT_ORDER if s in set(cfg.subject_names)]
    if not subjects:
        raise PyqBuildError("Choose at least one subject.")
    pool = eligible_rows(cfg, rows)
    slots = _slots(cfg, blueprint)
    present = {slot["subject"] for slot in slots}
    if present != set(subjects):
        raise PyqBuildError("Choose enough questions for every selected subject. A subject cannot be dropped to make the paper fit.")
    if not slots or any(slot["count"] <= 0 for slot in slots):
        raise PyqBuildError("The shortened mock needs at least one question in every section that the blueprint keeps. Raise the question count.")

    full_total = full_question_count(blueprint, len(subjects))
    if cfg.pyq_length == "short" and sum(slot["count"] for slot in slots) >= full_total:
        raise PyqBuildError("That question count is a full paper. Choose Full JEE-Style Mock, or lower the shortened count.")

    rng = random.Random(cfg.pyq_seed if cfg.pyq_seed is not None else random.randrange(1 << 30))
    seen = set(seen or ())
    year_use: Dict[Any, int] = {}
    used: Set[str] = set()
    shortages: List[Dict[str, Any]] = []
    chosen_rows: List[Dict[str, Any]] = []

    for slot in slots:
        formats = {_group(fmt) for fmt in slot["formats"]}
        candidates = []
        for row in pool:
            if row.get("subject") != slot["subject"] or row["content_id"] in used:
                continue
            group = _group(str(row.get("question_type") or ""))
            listed = _is_matching_list(row)
            row["matching_list"] = listed
            if slot["section_id"] == "matching":
                if listed and group in formats.union({"single_correct", "multiple_correct"}):
                    candidates.append(row)
            elif group in formats and not listed:
                candidates.append(row)
        picked = _pick_slot(candidates, slot["count"], seen, year_use, rng)
        if len(picked) < slot["count"]:
            shortages.append({
                "subject": slot["subject"],
                "title": slot["title"],
                "needed": slot["count"],
                "available": len(candidates),
                "formats": sorted(formats),
            })
            continue
        for row in picked:
            row = dict(row)
            row["paper_section"] = f"{slot['subject']} · {slot['title']}"
            row["section_label"] = slot["title"]
            row["instructions"] = slot["instructions"]
            row["marks"] = dict(slot["marks"])
            row["previously_seen"] = row["content_id"] in seen or (row.get("fingerprint") or "") in seen
            chosen_rows.append(row)
            used.add(row["content_id"])

    if shortages:
        raise PyqBuildError(_shortage_message(cfg.pyq_length, shortages, len(pool)))

    questions = [_to_question(row, i) for i, row in enumerate(chosen_rows, start=1)]
    _validate(questions, slots, cfg)

    by_subject: Dict[str, List[MockTestQuestion]] = {}
    for question in questions:
        by_subject.setdefault(question.section, []).append(question)
    for subject, count in _subject_targets(cfg, blueprint).items():
        sub = cfg.subject_config(subject)
        if sub is not None:
            sub.n_questions = count
    minutes = duration_minutes(
        blueprint, len(subjects), cfg.pyq_length,
        cfg.total_time_minutes if cfg.pyq_length == "short" else None,
    )
    cfg.total_time_minutes = minutes
    labels = _label(cfg, blueprint, len(subjects))
    max_marks = sum(float((getattr(q, "_marks", None) or {}).get("correct") or 0) for q in questions)
    scheme: Dict[str, Dict[str, Any]] = {}
    for slot in slots:
        for fmt in slot["formats"]:
            key = _group(fmt)
            if key == "single_correct":
                scheme.setdefault("single_correct", {k: v for k, v in slot["marks"].items() if k != "numeric_mode"})
                scheme.setdefault("comprehension", scheme["single_correct"])
            elif key == "multiple_correct":
                scheme.setdefault("multiple_correct", {k: v for k, v in slot["marks"].items() if k != "numeric_mode"})
            elif key in ("numeric", "integer"):
                cleaned = {k: v for k, v in slot["marks"].items() if k != "numeric_mode"}
                scheme.setdefault("numeric", cleaned)
                scheme.setdefault("integer", cleaned)
            elif key == "matching":
                scheme.setdefault("matching", {k: v for k, v in slot["marks"].items() if k != "numeric_mode"})

    years = sorted({int(q._source_year) for q in questions if getattr(q, "_source_year", None)})  # type: ignore[attr-defined]
    sections = [
        MockTestSection(
            subject=subject,
            chapter_ids=sorted({q.chapter_id for q in qs if q.chapter_id}),
            questions=qs,
        )
        for subject, qs in by_subject.items()
    ]
    meta = {
        "mock_type": "pyq",
        "coverage": "all",
        "blueprint_id": blueprint["id"],
        "blueprint_version": blueprint["version"],
        "blueprint": public_blueprint(blueprint),
        "sectional": labels["sectional"],
        "official_complete": labels["official_complete"],
        "length_label": labels["length_label"],
        "display_title": labels["display_title"],
        "disclaimer": labels["disclaimer"],
        "contains_unlearned": False,
        "personalised": False,
        "allocation": {},
        "learned_concepts": [],
        "year": None,
        "paper": blueprint.get("paper"),
        "organizing_iit": None,
        "pyq_length": cfg.pyq_length,
        "source_years": years,
        "syllabus_policy": SYLLABUS_POLICY,
        "genuine_pyq": True,
        "max_marks": max_marks,
        "time_limit_minutes": minutes,
        "marking_scheme": scheme,
        "pyq_seed": cfg.pyq_seed,
        "question_meta": {
            q.content_id: {
                "paper_section": q.paper_section,
                "section_label": getattr(q, "_section_label", ""),
                "instructions": q.instructions,
                "source_type": "PYQ",
                "previously_seen": q.previously_seen,
                "marks": getattr(q, "_marks", None),
                "source_year": getattr(q, "_source_year", None),
                "source_label": getattr(q, "_source_label", ""),
                "source_exam": getattr(q, "_source_exam", ""),
                "solution": getattr(q, "_solution", ""),
            }
            for q in questions
        },
        "section_plan": [
            {
                "subject": slot["subject"],
                "title": slot["title"],
                "count": slot["count"],
                "formats": sorted(slot["formats"]),
                "marks": {k: v for k, v in slot["marks"].items() if k != "numeric_mode"},
                "numeric_mode": slot["marks"].get("numeric_mode"),
            }
            for slot in slots
        ],
    }
    return {"sections": sections, "questions": questions, "meta": meta, "blueprint": blueprint}


def preview_from_rows(cfg: MockTestConfig, rows: Sequence[Dict[str, Any]], *, seen: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Same checks as a build, returned as a summary the configuration page can show."""
    try:
        paper = select_pyq(cfg, rows, seen=seen)
    except PyqBuildError as exc:
        return {"ok": False, "detail": str(exc)}
    meta = paper["meta"]
    questions = paper["questions"]
    chapters = []
    seen_chapters: Set[str] = set()
    concepts = []
    seen_concepts: Set[str] = set()
    for question in questions:
        if question.chapter_id and question.chapter_id not in seen_chapters:
            seen_chapters.add(question.chapter_id)
            chapters.append({"chapter_id": question.chapter_id, "title": question.chapter_title, "subject": question.section})
        if question.concept_id and question.concept_id not in seen_concepts:
            seen_concepts.add(question.concept_id)
            concepts.append({"concept_id": question.concept_id, "name": question.concept_name, "subject": question.section})
    return {
        "ok": True,
        "exam": cfg.exam,
        "exam_label": "JEE Main" if cfg.exam == "JEE_MAIN" else "JEE Advanced",
        "subjects": [s for s in SUBJECT_ORDER if s in set(cfg.subject_names)],
        "chapters_in_paper": chapters,
        "concepts_in_paper": concepts,
        "pyq_length": cfg.pyq_length,
        "length_label": meta["length_label"],
        "questions": len(questions),
        "duration_minutes": meta["time_limit_minutes"],
        "max_marks": meta["max_marks"],
        "sections": meta["section_plan"],
        "blueprint": meta["blueprint"],
        "source_years": meta["source_years"],
        "genuine_pyq": True,
        "sectional": meta["sectional"],
        "disclaimer": meta["disclaimer"],
        "syllabus_policy": SYLLABUS_POLICY,
    }
