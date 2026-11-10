"""One retrieval path for every mock type.

Smart and Custom read every gradeable question: PYQ, practice, and solved
examples that carry a discrete answer key. JEE PYQ Mock reads only official
JEE Advanced papers ingested from jeeadv.ac.in. Coaching papers and the
JEE Challenger set are stored as PYQ but are not an official paper.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

ELIGIBLE_TYPES = ("PYQ", "Practice Question", "Solved Example")
OFFICIAL_LICENSE_MARK = "official exam paper"

# Main mode does not serve Advanced-only papers. Advanced mode may use Main
# questions: they are real graded questions at a lower exam label.
_EXAM_SQL = {
    "JEE_MAIN": "(c.target_exam is null or c.target_exam in ('Jee main', 'Jee main and Advanced'))",
    "JEE_ADVANCED": "(c.target_exam is null or c.target_exam in ('Jee Advanced', 'Jee main', 'Jee main and Advanced'))",
    "custom": "true",
}


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


_ADV_OFFICIAL = re.compile(r"^JEE Advanced (\d{4}) Paper ([12])$")
_ADV_SHORT = re.compile(r"^JEE Adv (\d{4}) Paper ([12])$")
_ADV_BANK = re.compile(r"JEE_Advanced_(\d{4})_Paper_([12])")
_MAIN_BANK = re.compile(r"JEE Official Exam \((JEE_Main_\d{4}_[^)]+)\)")


def paper_key_for(source_book: str, license_: str = "") -> Optional[str]:
    """Identity of a real JEE paper. Coaching sheets are not papers."""
    if "coaching" in (license_ or "").lower():
        return None
    book = source_book or ""
    match = _ADV_OFFICIAL.match(book) or _ADV_SHORT.match(book) or _ADV_BANK.search(book)
    if match:
        return f"advanced:{match.group(1)}:{match.group(2)}"
    match = _MAIN_BANK.search(book)
    if match:
        return f"main:{match.group(1)}"
    return None


def paper_label(key: str) -> str:
    if key.startswith("advanced:"):
        _exam, year, paper = key.split(":")
        return f"JEE Advanced {year} Paper {paper}"
    if key.startswith("main:"):
        rest = key.split(":", 1)[1]
        parts = rest.split("_")
        # JEE_Main_2026_Apr_2nd_Shift_1
        year = parts[2] if len(parts) > 2 else ""
        when = " ".join(parts[3:]).replace("_", " ")
        return f"JEE Main {year} {when}".strip()
    return key


def is_genuine_official(row: Dict[str, Any]) -> bool:
    """Official JEE Advanced paper row, not a coaching paper labelled PYQ."""
    if (row.get("content_type") or "") != "PYQ":
        return False
    book = str(row.get("source_book") or "")
    license_ = str(row.get("license") or "").lower()
    return book.startswith("JEE Advanced ") and "Paper " in book and OFFICIAL_LICENSE_MARK in license_


def paper_name(year: int, paper: int) -> str:
    return f"JEE Advanced {int(year)} Paper {int(paper)}"


_IITJEE = re.compile(r"^IIT[-\s]?JEE\s+(\d{4})", re.I)


def pyq_source_sql(exam: str) -> str:
    """SQL fragment: genuine Main, Advanced, or pre-2013 IIT-JEE rows. Coaching is excluded by the caller."""
    if exam == "JEE_MAIN":
        return "(c.source_book ILIKE 'JEE Official Exam (JEE_Main_%%' OR c.source_book ~* '^JEE Main [0-9]{4}')"
    return (
        "(c.source_book ~ '^JEE Advanced [0-9]{4} Paper [12]$'"
        " OR c.source_book ~ '^JEE Adv [0-9]{4} Paper [12]$'"
        " OR c.source_book ILIKE 'JEE Official Exam (JEE_Advanced_%%'"
        " OR c.source_book ~* '^IIT[- ]JEE [0-9]{4}')"
    )


def source_of(source_book: str, license_: str = "") -> Optional[Dict[str, Any]]:
    """Where a PYQ actually came from. Years before 2013 are IIT-JEE, never JEE Advanced."""
    if "coaching" in (license_ or "").lower():
        return None
    key = paper_key_for(source_book, license_)
    if key and key.startswith("advanced:"):
        year, paper_no = int(key.split(":")[1]), int(key.split(":")[2])
        if year < 2013:
            return {
                "exam": "IIT_JEE",
                "year": year,
                "paper": paper_no,
                "key": f"iitjee:{year}:{paper_no}",
                "label": f"IIT-JEE {year} Paper {paper_no}",
            }
        return {
            "exam": "JEE_ADVANCED",
            "year": year,
            "paper": paper_no,
            "key": key,
            "label": f"JEE Advanced {year} Paper {paper_no}",
        }
    if key and key.startswith("main:"):
        parts = key.split(":", 1)[1].split("_")
        try:
            year = int(parts[2])
        except (IndexError, ValueError):
            year = None
        return {"exam": "JEE_MAIN", "year": year, "paper": None, "key": key, "label": paper_label(key)}
    match = _IITJEE.match(source_book or "")
    if match and int(match.group(1)) < 2013:
        year = int(match.group(1))
        return {"exam": "IIT_JEE", "year": year, "paper": None, "key": f"iitjee:{year}", "label": f"IIT-JEE {year}"}
    return None


def question_number(row: Dict[str, Any]) -> int:
    import re
    for text in (row.get("source_ref"), row.get("heading"), ""):
        match = re.search(r"\bQ\.?\s*(\d+)\b", str(text or ""), re.I)
        if match:
            return int(match.group(1))
    return 10_000


def normalise_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    answer = _loads(row.get("answer"))
    if not isinstance(answer, dict) or not answer.get("kind"):
        return None
    structured = _loads(row.get("structured")) or {}
    if not isinstance(structured, dict):
        structured = {}
    stem = (structured.get("stem") or row.get("core_text") or "").strip()
    if not stem:
        return None
    qtype = str(row.get("question_type") or "").strip().lower()
    options = None
    raw_options = structured.get("options")
    if isinstance(raw_options, list) and raw_options:
        options = [{"label": str(o.get("label", "")), "text": str(o.get("text", ""))} for o in raw_options]
    if qtype not in ("numeric", "integer", "numerical") and not options:
        return None
    return {
        "content_id": row["content_id"],
        "concept_id": row.get("concept_id") or row.get("primary_concept_id") or "",
        "secondary_concepts": row.get("secondary_concepts") or "",
        "question_type": qtype,
        "content_type": row.get("content_type") or "",
        "subject": row.get("subject") or "",
        "chapter_id": row.get("chapter_id") or "",
        "chapter_title": row.get("chapter_title") or row.get("chapter") or row.get("chapter_id") or "",
        "difficulty": int(row.get("difficulty") or 3),
        "stem": stem[:4000],
        "options": options,
        "answer": answer,
        "fingerprint": row.get("fingerprint") or "",
        "source_book": row.get("source_book") or "",
        "source_ref": row.get("source_ref") or "",
        "license": row.get("license") or "",
        "heading": row.get("heading") or "",
        "concept_name": row.get("concept_name") or "",
        "solution": (row.get("worked_solution") or "")[:6000],
    }


def fetch(cfg: Any, *, concept_ids: Optional[Sequence[str]] = None, store_mod: Any = None) -> List[Dict[str, Any]]:
    """Gradeable questions for this config. Answers stay on the row for the snapshot."""
    if store_mod is None:
        from retrieval import store as store_mod
    store = store_mod

    subjects = [s.subject for s in cfg.subjects]
    params: Dict[str, Any] = {"subjects": subjects}
    clauses = [
        "c.subject = ANY(%(subjects)s)",
        "sol.answer is not null",
        "(sol.answer ? 'kind')",
        store.FIGURE_READY_SQL,
        _EXAM_SQL.get(cfg.exam, "true"),
    ]
    if cfg.mock_type == "pyq":
        # Matching-list PYQs are gradeable when they carry options and a letter key.
        # Smart and Custom keep excluding them.
        clauses.append("c.question_type not in ('subjective')")
        clauses.append("c.content_type = 'PYQ'")
        clauses.append("c.license NOT ILIKE '%%coaching%%'")
        clauses.append(pyq_source_sql(cfg.exam))
        # A JEE Main mock must not be pulled in by a null target_exam on an Advanced row.
        # The source-book predicate above is the real exam gate; drop the loose exam SQL.
        clauses[:] = [c for c in clauses if c != _EXAM_SQL.get(cfg.exam, "true")]
        open_subjects = [s.subject for s in cfg.subjects if not (s.chapters or [])]
        chapters = [c for s in cfg.subjects for c in (s.chapters or [])]
        if chapters and open_subjects:
            clauses.append("(c.subject = ANY(%(open_subjects)s) OR c.chapter_id = ANY(%(chapters)s))")
            params["open_subjects"] = open_subjects
            params["chapters"] = chapters
        elif chapters:
            clauses.append("c.chapter_id = ANY(%(chapters)s)")
            params["chapters"] = chapters
    else:
        clauses.append("c.question_type not in ('subjective', 'matching')")
        clauses.append("c.content_type = ANY(%(types)s)")
        params["types"] = list(ELIGIBLE_TYPES)
        chapters = [c for s in cfg.subjects for c in (s.chapters or [])]
        if chapters and cfg.mock_type != "smart":
            clauses.append("c.chapter_id = ANY(%(chapters)s)")
            params["chapters"] = chapters
        if concept_ids:
            clauses.append("c.primary_concept_id = ANY(%(concepts)s)")
            params["concepts"] = list(concept_ids)

    limit = 12000 if cfg.mock_type == "pyq" else 1500
    concept_sql = (
        "k.concept_name, sol.worked_solution,"
        if cfg.mock_type == "pyq" else ""
    )
    concept_join = (
        "LEFT JOIN public.concepts k ON k.concept_id = c.primary_concept_id"
        if cfg.mock_type == "pyq" else ""
    )
    rows = store.query(
        f"""
        SELECT c.content_id, c.primary_concept_id AS concept_id, c.secondary_concepts,
               c.question_type, c.content_type, c.subject, c.chapter_id, c.chapter,
               {store.EFFECTIVE_DIFFICULTY_SQL} AS difficulty, c.core_text, c.fingerprint,
               c.structured::text AS structured, sol.answer::text AS answer,
               {concept_sql}
               c.source_book, c.source_ref, c.license, c.heading
        FROM public.content c
        JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
        {concept_join}
        WHERE {" AND ".join(clauses)}
        LIMIT {limit}
        """,
        params,
    )
    out = []
    for row in rows:
        item = normalise_row(row)
        if item is None:
            continue
        if cfg.mock_type == "pyq" and not source_of(item.get("source_book") or "", item.get("license") or ""):
            continue
        out.append(item)
    if cfg.mock_type != "pyq":
        out.extend(_from_worked_solutions(store, params, clauses, {item["content_id"] for item in out}))
    return out


def _from_worked_solutions(store: Any, params: Dict[str, Any], clauses: List[str], seen: set) -> List[Dict[str, Any]]:
    """Practice questions and solved examples whose printed final result is one number.

    The correct option is that printed result. The worked solution is not returned.
    """
    from mock_test_agent import settings as config
    from mock_test_agent.derived_items import from_cache, load_cached, make_single_correct, read_with_model, store_cached

    worked = [c for c in clauses if "sol.answer" not in c]
    worked.append("(sol.answer is null or not (sol.answer ? 'kind'))")
    worked.append("c.content_type in ('Practice Question', 'Solved Example')")
    worked.append("length(coalesce(sol.worked_solution, '')) > 40")
    rows = store.query(
        f"""
        SELECT c.content_id, c.primary_concept_id AS concept_id, c.secondary_concepts,
               c.question_type, c.content_type, c.subject, c.chapter_id, c.chapter,
               {store.EFFECTIVE_DIFFICULTY_SQL} AS difficulty, c.core_text, c.fingerprint,
               c.structured::text AS structured, sol.worked_solution,
               c.source_book, c.source_ref, c.license, c.heading
        FROM public.content c
        JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
        WHERE {" AND ".join(worked)}
        LIMIT 2500
        """,
        params,
    )
    found: List[Dict[str, Any]] = []
    cache = load_cached([row.get("content_id") for row in rows])
    reads_left = int(getattr(config, "MOCK_SOLUTION_READS", 12) or 0)
    for row in rows:
        if row.get("content_id") in seen:
            continue
        solution = row.get("worked_solution") or ""
        content_id = row["content_id"]
        cached = cache.get(content_id)
        if cached and cached.get("status") == "rejected":
            continue
        made = from_cache(solution, cached) if cached else None
        if made is None and not cached:
            made = make_single_correct(solution, content_id)
            if made is None and reads_left > 0:
                reads_left -= 1
                made = read_with_model(solution, content_id)
                store_cached(content_id, made)
        if not made:
            continue
        letter = made["answer"]["letters"]
        correct = next(option["text"] for option in made["options"] if option["label"] == letter)
        structured = _loads(row.get("structured")) or {}
        if not isinstance(structured, dict):
            structured = {}
        stem = (structured.get("stem") or row.get("core_text") or "")
        if ("." in correct or " " in correct) and correct in stem:
            continue
        structured = dict(structured)
        structured["options"] = made["options"]
        item = normalise_row({
            **row,
            "question_type": "single_correct",
            "structured": structured,
            "answer": made["answer"],
        })
        if item is None or "worked_solution" in item:
            continue
        item["derived_from_solution"] = True
        found.append(item)
    return found


def list_official_papers() -> List[Dict[str, Any]]:
    """Every stored JEE Main or Advanced paper that has an answer key.

    Official jeeadv.ac.in Physics rows and the wider JEE Official Exam set for the
    same paper are one entry. Coaching sheets are left out. 2027 is absent because
    those papers are not in the bank.
    """
    from retrieval import store

    rows = store.query(
        """
        SELECT c.source_book, c.subject, c.license, count(*) AS n
        FROM public.content c
        JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
        WHERE c.content_type = 'PYQ'
          AND sol.answer IS NOT NULL
          AND (sol.answer ? 'kind')
          AND c.license NOT ILIKE '%%coaching%%'
          AND (c.source_book ~ '^JEE Advanced [0-9]{4} Paper [12]$'
               OR c.source_book ~ '^JEE Adv [0-9]{4} Paper [12]$'
               OR c.source_book ILIKE 'JEE Official Exam (JEE_Advanced_%%'
               OR c.source_book ILIKE 'JEE Official Exam (JEE_Main_%%')
        GROUP BY c.source_book, c.subject, c.license
        ORDER BY c.source_book
        """,
    )
    papers: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = paper_key_for(str(row.get("source_book") or ""), str(row.get("license") or ""))
        if not key:
            continue
        if key.startswith("advanced:"):
            year, paper_no = int(key.split(":")[1]), int(key.split(":")[2])
            exam = "JEE_ADVANCED"
        else:
            year, paper_no = int(key.split("_")[2]), None
            exam = "JEE_MAIN"
        slot = papers.setdefault(key, {
            "key": key,
            "exam": exam,
            "year": year,
            "paper": paper_no,
            "label": paper_label(key),
            "subjects": {},
            "questions": 0,
        })
        subject = row.get("subject") or ""
        slot["subjects"][subject] = slot["subjects"].get(subject, 0) + int(row["n"])
        slot["questions"] += int(row["n"])
    return sorted(papers.values(), key=lambda p: (p["exam"], p["year"], p["label"]))


def pyq_syllabus(exam: str) -> List[Dict[str, Any]]:
    """Chapters and primary concepts that have a genuine PYQ for this exam."""
    from retrieval import store

    rows = store.query(
        f"""
        SELECT c.chapter_id, c.primary_concept_id AS concept_id, count(*) AS n
        FROM public.content c
        JOIN knowledge_private.solutions sol ON sol.question_id = c.content_id
        WHERE c.content_type = 'PYQ'
          AND c.license NOT ILIKE '%%coaching%%'
          AND sol.answer IS NOT NULL
          AND (sol.answer ? 'kind')
          AND c.question_type NOT IN ('subjective')
          AND {store.FIGURE_READY_SQL}
          AND {pyq_source_sql(exam)}
        GROUP BY c.chapter_id, c.primary_concept_id
        """,
    )
    chapters = {ch["chapter_id"]: ch for ch in store.chapters()}
    concepts = {c["concept_id"]: c for c in store.all_concepts()}
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        chapter = chapters.get(row.get("chapter_id")) or {}
        subject = chapter.get("subject") or ""
        if subject not in ("Physics", "Chemistry", "Mathematics"):
            continue
        slot = grouped.setdefault(row["chapter_id"], {
            "chapter_id": row["chapter_id"],
            "title": chapter.get("title") or row["chapter_id"],
            "subject": subject,
            "pyqs": 0,
            "concepts": [],
        })
        count = int(row["n"])
        slot["pyqs"] += count
        concept_id = row.get("concept_id") or ""
        if not concept_id:
            continue
        concept = concepts.get(concept_id) or {}
        slot["concepts"].append({
            "concept_id": concept_id,
            "name": concept.get("name") or concept_id,
            "pyqs": count,
        })
    for slot in grouped.values():
        slot["concepts"].sort(key=lambda item: item["name"])
    return sorted(grouped.values(), key=lambda item: (item["subject"], item["title"]))
