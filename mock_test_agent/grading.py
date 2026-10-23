"""Mock Test grading and analytics — entirely deterministic, no LLM.

Responsibilities:
  MARKING_SCHEMES         Built-in schemes for JEE_ADVANCED and JEE_MAIN.
  resolve_marking_scheme  Select the right scheme for a test config.
  apply_marking_scheme    Score a single question answer.
  grade_submission        Grade one raw submission dict against its question row.
  grade_all_submissions   Grade every submission in a test after /submit.
  aggregate_scores        Compute overall + per-subject totals.
  build_chapter_analytics Chapter-level accuracy breakdown.
  build_concept_analytics Concept-level accuracy breakdown.
  compute_result          Full analytics dict returned by /mock/result.

Grading sequence (called by the /submit route):
  1. For each mock_test_question row, look up the student's submission.
  2. grade_submission → grade_status, is_correct, question_score.
  3. Bulk-update mock_test_submissions with grades.
  4. call commit_progress() for each gradeable question so proficiency updates.
  5. Compute subject/chapter/concept analytics and update mock_tests row.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# Heavy service/DB imports are done lazily inside functions to avoid
# triggering services/__init__.py → turn_service → context_manager at
# module-load time (which requires openai and a live config).
# They are always available at call time.

logger = logging.getLogger("aeropt.mock_grading")


# ── Marking schemes ───────────────────────────────────────────────────────────

# Each scheme maps question_type → {correct, incorrect, unattempted, [partial]}.
# Values are integer points (may be negative for incorrect).
# partial is only meaningful for multiple_correct.

MARKING_SCHEMES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "JEE_ADVANCED": {
        "single_correct": {
            "correct": 3,
            "incorrect": -1,
            "unattempted": 0,
        },
        "multiple_correct": {
            "correct": 4,           # full marks: all correct, none wrong
            "partial": 1,           # one correct subset chosen, no wrong options
            "incorrect": -2,        # any wrong option selected
            "unattempted": 0,
        },
        "numeric": {
            "correct": 4,
            "incorrect": 0,
            "unattempted": 0,
        },
        "integer": {
            "correct": 3,
            "incorrect": 0,
            "unattempted": 0,
        },
        "comprehension": {
            "correct": 3,
            "incorrect": -1,
            "unattempted": 0,
        },
    },
    "JEE_MAIN": {
        "single_correct": {
            "correct": 4,
            "incorrect": -1,
            "unattempted": 0,
        },
        "multiple_correct": {
            # JEE Main dropped multiple-correct from 2019 onwards; treat as single
            "correct": 4,
            "incorrect": -1,
            "unattempted": 0,
        },
        "numeric": {
            "correct": 4,
            "incorrect": -1,
            "unattempted": 0,
        },
        "integer": {
            "correct": 4,
            "incorrect": 0,
            "unattempted": 0,
        },
        "comprehension": {
            "correct": 4,
            "incorrect": -1,
            "unattempted": 0,
        },
    },
}

# Fallback for question types not listed in a scheme
_DEFAULT_SCHEME: Dict[str, Any] = {"correct": 4, "incorrect": -1, "unattempted": 0}


def resolve_marking_scheme(
    exam: str,
    custom_scheme: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return the active marking scheme for this test.

    Precedence: custom_scheme (per-qtype override) > exam default > fallback.
    """
    base = MARKING_SCHEMES.get(exam, MARKING_SCHEMES["JEE_ADVANCED"])
    if not custom_scheme:
        return base
    # Merge: custom keys override the base; unlisted keys keep base defaults.
    merged = dict(base)
    merged.update(custom_scheme)
    return merged


# ── Per-question scoring ───────────────────────────────────────────────────────

def _qtype_normalised(question_type: str) -> str:
    """Map raw DB question_type strings to scheme keys."""
    mapping = {
        "SINGLE_CORRECT": "single_correct",
        "MCQ": "single_correct",
        "MULTI_CORRECT": "multiple_correct",
        "MULTIPLE_CORRECT": "multiple_correct",
        "NUMERIC": "numeric",
        "INTEGER": "integer",
        "COMPREHENSION": "comprehension",
        "exercise": "numeric",          # OpenStax exercises — treated as numeric
    }
    return mapping.get(question_type, question_type.lower())


def apply_marking_scheme(
    *,
    grade_status: str,
    is_correct: Optional[bool],
    question_type: str,
    student_choice: Optional[List[str]],
    answer: Optional[Dict[str, Any]],
    scheme: Dict[str, Dict[str, Any]],
) -> float:
    """Return the points scored for one question.

    For multiple_correct partial credit:
      - chosen is a subset of the correct set and no wrong options → partial
      - any wrong option chosen → incorrect (negative marks)
      - all correct options chosen → correct
    """
    qtype = _qtype_normalised(question_type)
    rules = scheme.get(qtype, _DEFAULT_SCHEME)

    if grade_status == "unattempted":
        return float(rules.get("unattempted", 0))

    if grade_status in ("ungradable", "invalid_submission"):
        return 0.0

    # Multiple-correct partial credit
    if qtype == "multiple_correct" and grade_status == "incorrect" and "partial" in rules:
        # Check if chosen is a non-empty subset of correct options (no wrong ones)
        if isinstance(answer, dict) and answer.get("kind") == "letters":
            correct_set = set(str(answer.get("value") or "").upper())
            chosen_set = set(c.upper() for c in (student_choice or []))
            if chosen_set and chosen_set.issubset(correct_set) and not chosen_set.symmetric_difference(correct_set) == chosen_set:
                # some correct options missing but none wrong → partial
                if not (chosen_set - correct_set):
                    return float(rules.get("partial", 1))

    if grade_status == "correct" or is_correct is True:
        return float(rules.get("correct", 4))
    if grade_status == "incorrect" or is_correct is False:
        return float(rules.get("incorrect", -1))

    return 0.0


def grade_submission(
    *,
    question_row: Dict[str, Any],
    submission: Optional[Dict[str, Any]],
    scheme: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Grade one question against its submission (or mark unattempted).

    Returns a dict with grade_status, is_correct, question_score, answer_text.
    """
    if not submission:
        qtype = _qtype_normalised(question_row.get("question_type") or "")
        rules = scheme.get(qtype, _DEFAULT_SCHEME)
        return {
            "grade_status": "unattempted",
            "is_correct": None,
            "question_score": float(rules.get("unattempted", 0)),
            "answer_text": "",
        }

    # Reconstruct the question dict that answer_key expects
    answer_raw = question_row.get("answer")
    if isinstance(answer_raw, str):
        try:
            answer_raw = json.loads(answer_raw)
        except (ValueError, TypeError):
            answer_raw = None

    # Build a question dict compatible with answer_key.grade_served_question
    q_for_grading: Dict[str, Any] = {
        "content_id": question_row.get("content_id"),
        "question_type": question_row.get("question_type"),
        "answer": answer_raw,
        "structured": {
            "answer": answer_raw,
            "options": question_row.get("options") or [],
        },
        "solution": "",     # answer_key never needs the worked solution for structured grading
    }

    # Reconstruct the choice / value from the submission
    choice = submission.get("choice")
    numeric_val = submission.get("numeric_value")
    student_answer_text = submission.get("student_answer") or ""

    if not question_row.get("has_gradekey") or not answer_raw:
        return {
            "grade_status": "ungradable",
            "is_correct": None,
            "question_score": 0.0,
            "answer_text": "",
        }

    from services.answer_key import grade_served_question, grade_free_answer  # lazy import
    qtype = (question_row.get("question_type") or "").lower()
    if qtype in ("numeric", "integer", "exercise"):
        decision = grade_served_question(q_for_grading, value=student_answer_text or str(numeric_val or ""))
    elif isinstance(choice, list) and choice:
        decision = grade_served_question(q_for_grading, choice=choice)
    else:
        # Try to parse the student_answer text as choice or numeric
        decision = grade_free_answer(q_for_grading, student_answer_text)

    grade_status = decision.get("grade_status", "ungradable")
    is_correct = decision.get("is_correct")
    answer_text = decision.get("answer_text") or ""

    question_score = apply_marking_scheme(
        grade_status=grade_status,
        is_correct=is_correct,
        question_type=question_row.get("question_type") or "",
        student_choice=choice,
        answer=answer_raw,
        scheme=scheme,
    )

    return {
        "grade_status": grade_status,
        "is_correct": is_correct,
        "question_score": question_score,
        "answer_text": answer_text,
    }


# ── Bulk grading ───────────────────────────────────────────────────────────────

def grade_all_submissions(
    *,
    mock_test_id: str,
    user_id: str,
    session_id: str,
    questions: List[Dict[str, Any]],
    submissions: List[Dict[str, Any]],
    scheme: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Grade every question, update mock_test_submissions, call commit_progress.

    Returns a list of graded-submission dicts (one per question, even unattempted).
    """
    sub_by_content: Dict[str, Dict[str, Any]] = {
        s["content_id"]: s for s in submissions
    }

    graded_rows: List[Dict[str, Any]] = []
    submission_updates: List[Dict[str, Any]] = []

    for q in questions:
        content_id = q["content_id"]
        sub = sub_by_content.get(content_id)
        result = grade_submission(question_row=q, submission=sub, scheme=scheme)

        graded_at_iso = _now_iso()
        attempt_id = str(uuid.uuid4())

        # Persist attempt to learner proficiency if answer is authoritative
        if result["grade_status"] in ("correct", "incorrect") and q.get("concept_id"):
            try:
                from services.grading import commit_progress as _commit  # lazy import
                gresult = _commit(  # type: ignore[call-arg]
                    user_id=user_id,
                    session_id=session_id,
                    concept_id=q["concept_id"],
                    content_id=content_id,
                    submission_id=attempt_id,
                    question_instance_id=None,
                    student_answer=sub.get("student_answer", "") if sub else "",
                    fingerprint=str(uuid.uuid4()),  # unique per attempt
                    is_correct=result["is_correct"],
                    grade_status=result["grade_status"],
                    evidence_source="answer_key",
                    source="mock_test",
                    study_session_id=None,
                    question_difficulty=q.get("difficulty"),
                    answer_text=result["answer_text"],
                )
                attempt_id = gresult.attempt_id or attempt_id
            except Exception:
                logger.warning("commit_progress failed for %r", content_id, exc_info=True)

        update = {
            "mock_test_id": mock_test_id,
            "content_id": content_id,
            "user_id": user_id,
            "grade_status": result["grade_status"],
            "is_correct": result["is_correct"],
            "question_score": result["question_score"],
            "answer_text": result["answer_text"],
            "graded_at": graded_at_iso,
            "attempt_id": attempt_id,
            "concept_id": q.get("concept_id"),
        }
        if sub is None:
            # Insert an unattempted row so analytics are complete
            update["student_answer"] = None
        submission_updates.append(update)

        graded_rows.append({
            **q,
            **result,
            "submission": sub,
        })

    # Bulk upsert graded submissions
    if submission_updates:
        try:
            from db.supabase_client import db as _db
            _db.client.table("mock_test_submissions").upsert(
                submission_updates,
                on_conflict="mock_test_id,content_id,user_id",
            ).execute()
        except Exception:
            logger.warning("bulk submission upsert failed", exc_info=True)

    return graded_rows


# ── Aggregation and analytics ─────────────────────────────────────────────────

def aggregate_scores(
    graded: List[Dict[str, Any]],
    scheme: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute overall totals from the graded-submission list."""
    raw_score = 0.0
    max_score = 0.0
    attempted = 0
    correct = 0

    for g in graded:
        qtype = _qtype_normalised(g.get("question_type") or "")
        rules = scheme.get(qtype, _DEFAULT_SCHEME)
        max_q = float(rules.get("correct", 4))
        max_score += max_q
        raw_score += float(g.get("question_score") or 0)
        if g.get("grade_status") not in ("unattempted", "ungradable"):
            attempted += 1
        if g.get("is_correct"):
            correct += 1

    accuracy = (correct / attempted * 100) if attempted > 0 else 0.0
    return {
        "raw_score": round(raw_score, 2),
        "max_score": round(max_score, 2),
        "accuracy_pct": round(accuracy, 1),
        "questions_total": len(graded),
        "questions_attempted": attempted,
        "questions_correct": correct,
    }


def build_subject_scores(graded: List[Dict[str, Any]], scheme: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Per-subject score breakdown."""
    by_subject: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for g in graded:
        by_subject[g.get("section") or g.get("subject") or "Unknown"].append(g)

    result: Dict[str, Any] = {}
    for subject, items in by_subject.items():
        agg = aggregate_scores(items, scheme)
        result[subject] = agg
    return result


def build_chapter_analytics(graded: List[Dict[str, Any]], scheme: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Per-chapter score and accuracy breakdown."""
    by_chapter: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    chapter_titles: Dict[str, str] = {}
    for g in graded:
        ch_id = g.get("chapter_id") or "unknown"
        by_chapter[ch_id].append(g)
        chapter_titles[ch_id] = g.get("chapter_title") or ch_id

    result: Dict[str, Any] = {}
    for ch_id, items in by_chapter.items():
        agg = aggregate_scores(items, scheme)
        result[ch_id] = {
            **agg,
            "chapter_title": chapter_titles[ch_id],
            "subject": items[0].get("section") or items[0].get("subject") or "",
        }
    return result


def build_concept_analytics(graded: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-concept accuracy breakdown (does not weight by score — just correct rate)."""
    by_concept: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    concept_names: Dict[str, str] = {}
    for g in graded:
        c_id = g.get("concept_id") or "unknown"
        by_concept[c_id].append(g)
        concept_names[c_id] = g.get("concept_name") or c_id

    result: Dict[str, Any] = {}
    for c_id, items in by_concept.items():
        attempted = sum(1 for i in items if i.get("grade_status") not in ("unattempted", "ungradable"))
        correct = sum(1 for i in items if i.get("is_correct"))
        accuracy = (correct / attempted * 100) if attempted > 0 else 0.0
        result[c_id] = {
            "concept_name": concept_names[c_id],
            "total": len(items),
            "attempted": attempted,
            "correct": correct,
            "accuracy_pct": round(accuracy, 1),
        }
    return result


def _weak_concepts(concept_analytics: Dict[str, Any], threshold_pct: float = 50.0) -> List[Dict[str, Any]]:
    """Return concepts with accuracy below threshold, sorted ascending."""
    weak = [
        {"concept_id": c_id, "concept_name": v["concept_name"], "accuracy_pct": v["accuracy_pct"]}
        for c_id, v in concept_analytics.items()
        if v["attempted"] > 0 and v["accuracy_pct"] < threshold_pct
    ]
    return sorted(weak, key=lambda x: x["accuracy_pct"])


def _strong_weak_chapters(chapter_analytics: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Return (strong_chapter_ids, weak_chapter_ids) split at 60% accuracy."""
    strong, weak = [], []
    for ch_id, v in chapter_analytics.items():
        if v["questions_attempted"] == 0:
            continue
        if v["accuracy_pct"] >= 60:
            strong.append(ch_id)
        else:
            weak.append(ch_id)
    return strong, weak


# ── Full result computation ───────────────────────────────────────────────────

def compute_result(
    *,
    mock_test_id: str,
    user_id: str,
    session_id: str,
    test_row: Dict[str, Any],
    questions: List[Dict[str, Any]],
    submissions: List[Dict[str, Any]],
    custom_scheme: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Grade, aggregate, persist, and return the full result dict.

    Called once on /submit. Subsequent calls to /result read from the stored
    mock_tests row and do not re-grade.
    """
    exam = test_row.get("exam", "JEE_ADVANCED")
    scheme = resolve_marking_scheme(exam, custom_scheme)

    graded = grade_all_submissions(
        mock_test_id=mock_test_id,
        user_id=user_id,
        session_id=session_id,
        questions=questions,
        submissions=submissions,
        scheme=scheme,
    )

    overall = aggregate_scores(graded, scheme)
    subject_scores = build_subject_scores(graded, scheme)
    chapter_scores = build_chapter_analytics(graded, scheme)
    concept_scores = build_concept_analytics(graded)
    weak = _weak_concepts(concept_scores)
    strong_ch, weak_ch = _strong_weak_chapters(chapter_scores)

    # Persist aggregates back to mock_tests
    try:
        from db.supabase_client import db as _db
        _db.client.table("mock_tests").update({  # type: ignore[union-attr]
            "status": "graded",
            "raw_score": overall["raw_score"],
            "max_score": overall["max_score"],
            "accuracy_pct": overall["accuracy_pct"],
            "subject_scores": subject_scores,
            "chapter_scores": chapter_scores,
        }).eq("mock_test_id", mock_test_id).execute()
    except Exception:
        logger.warning("failed to persist mock_tests aggregates", exc_info=True)

    return {
        "mock_test_id": mock_test_id,
        "status": "graded",
        "exam": exam,
        "overall": overall,
        "subject_scores": subject_scores,
        "chapter_scores": chapter_scores,
        "concept_scores": concept_scores,
        "weak_concepts": weak,
        "strong_chapters": strong_ch,
        "weak_chapters": weak_ch,
        "graded_questions": [
            {
                "content_id": g["content_id"],
                "display_order": g.get("display_order"),
                "section": g.get("section") or g.get("subject"),
                "chapter_id": g.get("chapter_id"),
                "concept_id": g.get("concept_id"),
                "question_type": g.get("question_type"),
                "difficulty": g.get("difficulty"),
                "grade_status": g.get("grade_status"),
                "is_correct": g.get("is_correct"),
                "question_score": g.get("question_score"),
                "answer_text": g.get("answer_text"),
                "student_answer": (g.get("submission") or {}).get("student_answer"),
            }
            for g in graded
        ],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def max_score_for_scheme(scheme: Dict[str, Dict[str, Any]], question_type: str) -> float:
    """Maximum points a single question of this type can earn."""
    qtype = _qtype_normalised(question_type)
    rules = scheme.get(qtype, _DEFAULT_SCHEME)
    return float(rules.get("correct", 4))
