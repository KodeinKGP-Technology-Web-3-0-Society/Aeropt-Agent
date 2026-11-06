"""Mock Test Debrief Agent.

Generates a post-test narrative for the student: overall verdict, per-subject
highlights, identified weak areas, and a prioritised follow-up plan.

Design contract:
  * Called at most once per mock test (result is cached in mock_tests.debrief).
  * One LLM call per test — not per question.
  * Failure is non-fatal: the result endpoint always returns even if the debrief
    call errors out.
  * Adapts evaluation_agent.py's pattern: deterministic analytics first, then
    a single LLM call to narrate them.
  * Follow-up concept recommendations are drawn from weak concepts identified
    by mock_grading.build_concept_analytics, not invented by the model.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from db.supabase_client import db
from mock_test_agent import settings as config
from utils.llm import complete_text

logger = logging.getLogger("aeropt.mock_debrief")

# ── Prompt template ───────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a JEE coach writing the debrief after one mock test. "
    "Use only the facts in the brief. Name the chapters and concepts that appear there. "
    "Explain the score from marks earned, marks lost to wrong answers, and questions left blank. "
    "Do not predict a rank, do not praise effort, and do not write filler such as "
    "'struggling with depth' unless you name the idea that failed. "
    "Write Markdown with the three ## headings you are given, then bullets. "
    "Use LaTeX only for a formula that is already in the brief, as \\( … \\). "
    "Address the student as you. Stay under 320 words."
)

_DEBRIEF_PROMPT = """
Facts from this paper. Do not add a count, a chapter, or a concept that is not written here.

{brief}

Write exactly these three sections.

## What decided the score
Three or four sentences. Say whether wrong answers (negative marks) or blank questions did the damage, using the earned and lost figures. If one question type is weaker, cite its correct/wrong count.

## The pattern in the mistakes
Up to five bullets. Each bullet is one miss, or one concept that was missed more than once: name the concept and chapter, what you chose when that is listed, what the key was, and the idea to repair. Group misses that share a chapter. Do not invent a mistake that is not in the miss list.

## What to study next
Three bullets. Each names a concept or chapter from this paper and one action: re-derive the relation you missed, redo that multi-correct set, or attempt the questions you left blank in that chapter before starting another paper. Do not say "revise the syllabus".
"""


# ── Analytics helpers ─────────────────────────────────────────────────────────

def _subject_table(subject_scores: Dict[str, Any]) -> str:
    if not subject_scores:
        return "No subject breakdown available."
    rows = ["| Subject | Score | Max | Accuracy |", "|---|---|---|---|"]
    for subj, v in subject_scores.items():
        rows.append(
            f"| {subj} | {v.get('raw_score', 0)} | {v.get('max_score', 0)} "
            f"| {v.get('accuracy_pct', 0):.1f}% |"
        )
    return "\n".join(rows)


def _chapter_table(chapter_scores: Dict[str, Any]) -> str:
    if not chapter_scores:
        return "No chapter breakdown available."
    rows = ["| Chapter | Subject | Score | Accuracy | Attempted |", "|---|---|---|---|---|"]
    for _, v in sorted(chapter_scores.items(), key=lambda x: x[1].get("accuracy_pct", 100)):
        rows.append(
            f"| {v.get('chapter_title', '?')} | {v.get('subject', '')} "
            f"| {v.get('raw_score', 0)} | {v.get('accuracy_pct', 0):.1f}% "
            f"| {v.get('questions_attempted', 0)}/{v.get('questions_total', 0)} |"
        )
    return "\n".join(rows)


def _weak_concept_list(graded_questions: List[Dict[str, Any]]) -> str:
    """Build a concept-level accuracy summary for concepts below 50%."""
    by_concept: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"correct": 0, "total": 0, "name": ""})
    for q in graded_questions:
        c_id = q.get("concept_id") or "unknown"
        by_concept[c_id]["name"] = q.get("concept_name") or c_id
        by_concept[c_id]["total"] += 1
        if q.get("is_correct"):
            by_concept[c_id]["correct"] += 1

    weak = [
        (v["name"], v["correct"], v["total"])
        for v in by_concept.values()
        if v["total"] > 0 and v["correct"] / v["total"] < 0.5
    ]
    weak.sort(key=lambda x: x[1] / x[2])

    if not weak:
        return "No concepts fell below 50% accuracy — strong overall."
    lines = [f"- **{name}**: {correct}/{total} ({correct/total*100:.0f}%)" for name, correct, total in weak[:8]]
    return "\n".join(lines)


def _strong_chapters_list(chapter_scores: Dict[str, Any]) -> str:
    strong = [
        v.get("chapter_title", k)
        for k, v in chapter_scores.items()
        if v.get("questions_attempted", 0) > 0 and v.get("accuracy_pct", 0) >= 60
    ]
    if not strong:
        return "No chapters exceeded 60% accuracy — all need revision."
    return ", ".join(strong[:6])


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _question_kind(raw: Optional[str]) -> str:
    low = (raw or "").strip().lower()
    if low in ("numeric", "numerical", "integer"):
        return "numerical"
    if low in ("multiple_correct", "multi_correct"):
        return "more than one correct"
    if low in ("comprehension",):
        return "comprehension"
    return "single correct"


def _their_answer(q: Dict[str, Any]) -> str:
    choice = q.get("choice")
    if isinstance(choice, list) and choice:
        return ", ".join(str(c) for c in choice)
    if isinstance(choice, str) and choice.strip():
        return choice.strip()
    if q.get("numeric_value") is not None:
        return str(q.get("numeric_value"))
    text = str(q.get("student_answer") or "").strip()
    return text[:80]


def _minutes_between(start: Any, end: Any) -> Optional[int]:
    if not start or not end:
        return None
    from datetime import datetime

    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((b - a).total_seconds() // 60))


def build_evidence_brief(
    *,
    exam: str,
    raw_score: float,
    max_score: float,
    accuracy: float,
    graded_questions: List[Dict[str, Any]],
    subject_scores: Dict[str, Any],
    time_limit_minutes: Optional[int] = None,
    started_at: Any = None,
    submitted_at: Any = None,
) -> str:
    """Facts the debrief is allowed to mention. Counts come from the graded rows, not the model."""

    def _outcome(q: Dict[str, Any]) -> str:
        if q.get("is_correct") is True or q.get("grade_status") == "correct":
            return "correct"
        if q.get("grade_status") == "unattempted":
            return "skipped"
        if q.get("grade_status") == "incorrect" or q.get("is_correct") is False:
            return "wrong"
        return "other"

    correct = [q for q in graded_questions if _outcome(q) == "correct"]
    wrong = [q for q in graded_questions if _outcome(q) == "wrong"]
    skipped = [q for q in graded_questions if _outcome(q) == "skipped"]
    earned = sum(_as_float(q.get("question_score")) for q in correct)
    lost = -sum(_as_float(q.get("question_score")) for q in wrong if _as_float(q.get("question_score")) < 0)

    lines = [
        f"Exam: {exam}",
        f"Score: {raw_score:.1f} / {max_score:.1f} ({accuracy:.1f}% of questions correct).",
        f"Marks from correct answers: +{earned:.1f}. Marks lost to wrong answers: -{lost:.1f}.",
        f"Correct {len(correct)}, wrong {len(wrong)}, skipped {len(skipped)}, of {len(graded_questions)} questions.",
    ]
    used = _minutes_between(started_at, submitted_at)
    if used is not None and time_limit_minutes:
        lines.append(f"Time: {used} min of {time_limit_minutes} min.")
    elif time_limit_minutes:
        lines.append(f"Time limit: {time_limit_minutes} min. Time used was not recorded.")

    if subject_scores:
        lines.append("Subjects:")
        for name, row in subject_scores.items():
            lines.append(
                f"- {name}: {_as_float(row.get('raw_score')):.1f} / {_as_float(row.get('max_score')):.1f}, "
                f"{_as_float(row.get('accuracy_pct')):.0f}% correct"
            )

    by_kind: Dict[str, List[int]] = defaultdict(lambda: [0, 0, 0])
    for q in graded_questions:
        bucket = by_kind[_question_kind(q.get("question_type"))]
        outcome = _outcome(q)
        if outcome == "correct":
            bucket[0] += 1
        elif outcome == "wrong":
            bucket[1] += 1
        elif outcome == "skipped":
            bucket[2] += 1
    if by_kind:
        lines.append("Question types (correct / wrong / skipped):")
        for kind, (ok, bad, blank) in sorted(by_kind.items()):
            lines.append(f"- {kind}: {ok} / {bad} / {blank}")

    by_chapter: Dict[str, Dict[str, Any]] = {}
    for q in graded_questions:
        title = q.get("chapter_title") or q.get("chapter_id") or "Unknown chapter"
        row = by_chapter.setdefault(title, {"subject": q.get("section") or "", "ok": 0, "bad": 0, "blank": 0, "score": 0.0})
        row["score"] += _as_float(q.get("question_score"))
        outcome = _outcome(q)
        if outcome == "correct":
            row["ok"] += 1
        elif outcome == "wrong":
            row["bad"] += 1
        elif outcome == "skipped":
            row["blank"] += 1
    if by_chapter:
        lines.append("Chapters (correct, wrong, skipped, marks):")
        ordered = sorted(by_chapter.items(), key=lambda item: (item[1]["score"], -item[1]["bad"]))
        for title, row in ordered[:12]:
            subject = f"{row['subject']} · " if row["subject"] else ""
            lines.append(
                f"- {subject}{title}: {row['ok']} correct, {row['bad']} wrong, {row['blank']} skipped, marks {row['score']:+.1f}"
            )

    misses = sorted(wrong, key=lambda q: _as_float(q.get("question_score")))
    if misses:
        lines.append("Misses, worst marks first. 'You chose' is omitted when the paper did not store a choice:")
        for q in misses[:8]:
            concept = q.get("concept_name") or q.get("concept_id") or "this concept"
            chapter = q.get("chapter_title") or q.get("section") or ""
            chosen = _their_answer(q)
            key = str(q.get("answer_text") or "").strip()
            bits = [
                f"- {chapter} · {concept} · {_question_kind(q.get('question_type'))} · marks {_as_float(q.get('question_score')):+.1f}"
            ]
            if chosen:
                bits.append(f"you chose {chosen}")
            if key:
                bits.append(f"key {key[:80]}")
            lines.append(" · ".join(bits))
    else:
        lines.append("Misses: none. Every attempted question was correct.")

    if skipped:
        skipped_names = []
        for q in skipped[:6]:
            skipped_names.append(q.get("concept_name") or q.get("chapter_title") or "a question")
        lines.append("Left blank: " + "; ".join(str(n) for n in skipped_names) + ".")

    return "\n".join(lines)


def _follow_up_concepts(graded_questions: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    """Return concept_ids for the weakest concepts — used by Study Room integration."""
    by_concept: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"correct": 0, "total": 0})
    for q in graded_questions:
        c_id = q.get("concept_id") or ""
        if not c_id:
            continue
        by_concept[c_id]["total"] += 1
        if q.get("is_correct"):
            by_concept[c_id]["correct"] += 1

    weak = sorted(
        [
            (c_id, v["correct"] / v["total"])
            for c_id, v in by_concept.items()
            if v["total"] > 0
        ],
        key=lambda x: x[1],
    )
    return [c_id for c_id, _ in weak[:limit]]


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_debrief(
    *,
    mock_test_id: str,
    user_id: str,
    test_row: Dict[str, Any],
    graded_questions: List[Dict[str, Any]],
    subject_scores: Dict[str, Any],
    chapter_scores: Dict[str, Any],
) -> Optional[str]:
    """Generate and cache the LLM debrief narrative.

    Returns the debrief string, or None on failure.
    Persists the result to mock_tests.debrief so subsequent /result calls
    return the cached text without re-running the model.
    """
    overall = test_row
    raw_score = float(overall.get("raw_score") or 0)
    max_score = float(overall.get("max_score") or 0)
    accuracy = float(overall.get("accuracy_pct") or 0)
    # Chapter rollups in the brief are counted from the graded rows. chapter_scores
    # stays in the signature so older callers do not break.
    del chapter_scores

    brief = build_evidence_brief(
        exam=str(test_row.get("exam") or "JEE"),
        raw_score=raw_score,
        max_score=max_score,
        accuracy=accuracy,
        graded_questions=graded_questions,
        subject_scores=subject_scores,
        time_limit_minutes=test_row.get("time_limit_minutes"),
        started_at=test_row.get("started_at"),
        submitted_at=test_row.get("submitted_at"),
    )
    prompt = _DEBRIEF_PROMPT.format(brief=brief).strip()

    try:
        text, _ = complete_text(
            client=None,
            model=config.MOCK_DEBRIEF_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            fallback_models=config.MOCK_DEBRIEF_FALLBACK_MODELS,
            temperature=0.3,
            max_tokens=1200,
            min_chars=180,
            lane="background",
        )
        debrief = text.strip()
    except Exception:
        logger.warning("LLM debrief call failed for mock %r", mock_test_id, exc_info=True)
        # Generate a minimal deterministic debrief as fallback
        debrief = (
            "## What decided the score\n\n"
            "A written debrief could not be generated. These are the facts from your paper:\n\n"
            + brief
        )

    # Persist to DB so the result endpoint caches it
    from datetime import datetime, timezone
    try:
        db.client.table("mock_tests").update({
            "debrief": debrief,
            "debrief_generated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("mock_test_id", mock_test_id).execute()
    except Exception:
        logger.warning("could not persist debrief to mock_tests", exc_info=True)

    return debrief


def get_follow_up_recommendations(
    *,
    mock_test_id: str,
    user_id: str,
    graded_questions: List[Dict[str, Any]],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return Study Room concept recommendations for the weakest concepts.

    Each item includes the concept_id and concept_name so the frontend can
    link directly to a Study Room session for that concept.
    """
    from retrieval import store

    concept_ids = _follow_up_concepts(graded_questions, limit=limit)
    recommendations = []
    for c_id in concept_ids:
        row = store.concept(c_id)
        if row:
            recommendations.append({
                "concept_id": c_id,
                "concept_name": row.get("name", c_id),
                "chapter": row.get("chapter", ""),
                "subject": row.get("subject", ""),
            })
    return recommendations
