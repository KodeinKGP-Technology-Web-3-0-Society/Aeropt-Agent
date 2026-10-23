"""
Mock Test data models.

MockTestConfig  — the validated input that describes what test to build.
MockTestQuestion — one question as it appears in the assembled test (no answer).
MockTestSection  — a group of questions for one subject.
MockTestState    — a TypedDict parallel to StudyRoomState; passed through the
                   mock test pipeline without a LangGraph graph (the pipeline is
                   a plain function chain, so the state is just a convenience
                   holder used by mock_debrief.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


# ── Subject config item ───────────────────────────────────────────────────────

@dataclass
class SubjectConfig:
    """Per-subject section of a MockTestConfig."""
    subject: str                          # 'Physics' | 'Chemistry' | 'Mathematics'
    chapters: List[str] = field(default_factory=list)   # chapter_ids; empty = all
    n_questions: int = 30                 # questions to include from this subject
    difficulty_target: Optional[int] = None  # 1–5; None → exam default


# ── Top-level config ─────────────────────────────────────────────────────────

@dataclass
class MockTestConfig:
    """Validated configuration that drives the entire mock test pipeline."""
    user_id: str
    exam: str                             # 'JEE_ADVANCED' | 'JEE_MAIN' | 'custom'
    subjects: List[SubjectConfig] = field(default_factory=list)
    total_time_minutes: int = 180
    marking_scheme: Optional[Dict[str, Dict[str, Any]]] = None  # None = exam default
    exclude_seen: bool = False            # skip questions already in served_items
    force_new: bool = False               # ignore any in-progress test for this user
    allow_fallback: bool = True           # fill gaps from adjacent chapters

    # ── Convenience ───────────────────────────────────────────────────────────
    @property
    def total_questions(self) -> int:
        return sum(s.n_questions for s in self.subjects)

    @property
    def subject_names(self) -> List[str]:
        return [s.subject for s in self.subjects]

    def subject_config(self, subject: str) -> Optional[SubjectConfig]:
        for s in self.subjects:
            if s.subject == subject:
                return s
        return None

    # ── Serialisation (for jsonb storage) ────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "exam": self.exam,
            "subjects": [
                {
                    "subject": s.subject,
                    "chapters": s.chapters,
                    "n_questions": s.n_questions,
                    "difficulty_target": s.difficulty_target,
                }
                for s in self.subjects
            ],
            "total_time_minutes": self.total_time_minutes,
            "marking_scheme": self.marking_scheme,
            "exclude_seen": self.exclude_seen,
            "force_new": self.force_new,
            "allow_fallback": self.allow_fallback,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MockTestConfig":
        subjects = [
            SubjectConfig(
                subject=s["subject"],
                chapters=s.get("chapters") or [],
                n_questions=int(s.get("n_questions") or 30),
                difficulty_target=s.get("difficulty_target"),
            )
            for s in (data.get("subjects") or [])
        ]
        return cls(
            user_id=data["user_id"],
            exam=data.get("exam", "JEE_ADVANCED"),
            subjects=subjects,
            total_time_minutes=int(data.get("total_time_minutes") or 180),
            marking_scheme=data.get("marking_scheme"),
            exclude_seen=bool(data.get("exclude_seen", False)),
            force_new=bool(data.get("force_new", False)),
            allow_fallback=bool(data.get("allow_fallback", True)),
        )


# ── Assembled question (student-facing, no answer) ───────────────────────────

@dataclass
class MockTestQuestion:
    """One question as shown to the student. The answer is never included."""
    content_id: str
    display_order: int                    # 1-based within the full test
    section: str                          # 'Physics' | 'Chemistry' | 'Mathematics'
    chapter_id: str
    chapter_title: str
    concept_id: str
    concept_name: str
    question_type: str                    # single_correct | numeric | multiple_correct | integer | comprehension
    difficulty: int
    stem: str
    options: Optional[List[Dict[str, str]]] = None   # [{"label": "A", "text": "…"}, …]
    has_gradekey: bool = True             # False → ungradable (subjective/matching)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_id": self.content_id,
            "display_order": self.display_order,
            "section": self.section,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "concept_id": self.concept_id,
            "concept_name": self.concept_name,
            "question_type": self.question_type,
            "difficulty": self.difficulty,
            "stem": self.stem,
            "options": self.options,
            "has_gradekey": self.has_gradekey,
        }


# ── Built test output ─────────────────────────────────────────────────────────

@dataclass
class MockTestSection:
    """Questions for one subject in the assembled test."""
    subject: str
    chapter_ids: List[str]
    questions: List[MockTestQuestion] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "chapter_ids": self.chapter_ids,
            "questions": [q.to_dict() for q in self.questions],
        }


@dataclass
class FeasibilityGap:
    """A chapter that could not be fully satisfied."""
    chapter_id: str
    chapter_title: str
    subject: str
    requested: int
    available: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "subject": self.subject,
            "requested": self.requested,
            "available": self.available,
        }


@dataclass
class FeasibilityReport:
    requested: int
    available: int
    gaps: List[FeasibilityGap] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def satisfiable(self) -> bool:
        return self.available >= self.requested

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "available": self.available,
            "satisfiable": self.satisfiable,
            "gaps": [g.to_dict() for g in self.gaps],
            "warnings": self.warnings,
        }


# ── MockTestState (lightweight holder for the debrief agent) ─────────────────

class MockTestState(TypedDict, total=False):
    """State bag used by mock_debrief.py. Not a LangGraph graph state."""
    # Core identifiers
    mock_test_id: str
    user_id: str

    # Config echo
    exam: str
    subjects: List[str]
    total_time_minutes: int

    # Session timing
    started_at: Optional[str]
    submitted_at: Optional[str]
    time_elapsed_minutes: Optional[float]

    # Results (filled after submit)
    raw_score: float
    max_score: float
    accuracy_pct: float
    total_questions: int
    questions_attempted: int
    questions_correct: int

    # Breakdown
    subject_scores: Dict[str, Any]
    chapter_scores: Dict[str, Any]
    concept_scores: Dict[str, Any]
    weak_concepts: List[Dict[str, Any]]    # [{concept_id, concept_name, accuracy_pct}, …]
    strong_chapters: List[str]
    weak_chapters: List[str]

    # Submission detail (for debrief agent to narrate)
    submissions: List[Dict[str, Any]]

    # Output from debrief agent
    debrief: Optional[str]
    follow_up_concepts: List[str]          # concept_ids recommended for review
    agent_used: str
    model_used: str
    error: Optional[str]
