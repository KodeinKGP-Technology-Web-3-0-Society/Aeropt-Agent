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
    "You are a JEE expert tutor writing a concise post-test report for a student "
    "who just completed a mock test. You are direct, encouraging, and specific. "
    "Use plain Markdown. Use LaTeX only for formulae (\\( … \\) inline, \\[ … \\] block). "
    "Never invent question numbers, paper names, or statistics that are not in the data."
)

_DEBRIEF_PROMPT = """
## Mock Test Report

**Exam:** {exam}
**Subjects tested:** {subjects}
**Total score:** {raw_score} / {max_score}  ({accuracy_pct}%)
**Questions:** {correct} correct out of {attempted} attempted ({total} total)

### Subject breakdown
{subject_table}

### Chapter performance
{chapter_table}

### Weakest concepts (accuracy < 50%)
{weak_list}

### Strong chapters (accuracy ≥ 60%)
{strong_list}

---

Write a performance report for the student with exactly these four sections:

**1. Overall verdict** (2–3 sentences): Acknowledge the score and whether it suggests JEE readiness.

**2. Subject analysis** (one sentence per subject): What the score reveals about their preparation in each subject.

**3. Priority revision areas** (bullet list, max 5): Name the weakest concepts or chapters and why they need work. Be specific.

**4. Next steps** (3 bullet points): Concrete actions — which Study Room topics to revisit, what question types to practise, and one time-management tip.

Keep the entire report under 300 words. Address the student as "you".
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
    # Build analytics for the prompt (deterministic)
    overall = test_row
    subjects = ", ".join(sorted(subject_scores.keys())) or "all subjects"
    raw_score = overall.get("raw_score") or 0
    max_score = overall.get("max_score") or 0
    accuracy = overall.get("accuracy_pct") or 0
    attempted = sum(
        1 for q in graded_questions
        if q.get("grade_status") not in ("unattempted", "ungradable", None)
    )
    correct = sum(1 for q in graded_questions if q.get("is_correct"))
    total = len(graded_questions)

    prompt = _DEBRIEF_PROMPT.format(
        exam=test_row.get("exam", "JEE"),
        subjects=subjects,
        raw_score=round(float(raw_score), 1),
        max_score=round(float(max_score), 1),
        accuracy_pct=round(float(accuracy), 1),
        correct=correct,
        attempted=attempted,
        total=total,
        subject_table=_subject_table(subject_scores),
        chapter_table=_chapter_table(chapter_scores),
        weak_list=_weak_concept_list(graded_questions),
        strong_list=_strong_chapters_list(chapter_scores),
    ).strip()

    try:
        text, _ = complete_text(
            client=None,
            model=config.MOCK_DEBRIEF_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            fallback_models=config.MOCK_DEBRIEF_FALLBACK_MODELS,
            temperature=0.4,
            max_tokens=700,
            min_chars=100,
            lane="background",
        )
        debrief = text.strip()
    except Exception:
        logger.warning("LLM debrief call failed for mock %r", mock_test_id, exc_info=True)
        # Generate a minimal deterministic debrief as fallback
        debrief = (
            f"**Score:** {raw_score:.0f} / {max_score:.0f} ({accuracy:.1f}%)\n\n"
            f"You answered {correct} out of {attempted} attempted questions correctly.\n\n"
            + (
                f"**Priority revision:** {_weak_concept_list(graded_questions)}"
                if graded_questions else ""
            )
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
