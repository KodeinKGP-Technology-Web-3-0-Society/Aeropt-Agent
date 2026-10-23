"""Tests for the Mock Test system.

Covers:
  - Config validation (services/mock_test.py::validate_config)
  - Marking scheme application (services/mock_grading.py)
  - Per-question scoring including partial credit
  - Aggregation and analytics
  - Feasibility report shape
  - Debrief analytics helpers (agents/mock_debrief.py)
  - Integration stub: build→start→answer→submit→result flow using in-memory fakes

These tests run offline with no database or LLM dependencies.
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# ── helpers ────────────────────────────────────────────────────────────────────

def _q(
    content_id: str = "q1",
    question_type: str = "single_correct",
    difficulty: int = 3,
    chapter_id: str = "optics",
    chapter_title: str = "Optics",
    concept_id: str = "phy_optics_refraction",
    concept_name: str = "Refraction",
    section: str = "Physics",
    has_gradekey: bool = True,
    answer: Optional[Dict[str, Any]] = None,
    options: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Build a minimal mock_test_questions row for testing."""
    return {
        "content_id": content_id,
        "display_order": 1,
        "section": section,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "concept_id": concept_id,
        "concept_name": concept_name,
        "question_type": question_type,
        "difficulty": difficulty,
        "stem": "Test question stem",
        "options": options or [
            {"label": "A", "text": "Option A"},
            {"label": "B", "text": "Option B"},
            {"label": "C", "text": "Option C"},
            {"label": "D", "text": "Option D"},
        ],
        "answer": json.dumps(answer) if answer else json.dumps({"kind": "letters", "value": "A"}),
        "has_gradekey": has_gradekey,
    }


def _sub(
    content_id: str = "q1",
    student_answer: str = "A",
    choice: Optional[List[str]] = None,
    numeric_value: Optional[float] = None,
    grade_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal mock_test_submissions row for testing."""
    return {
        "content_id": content_id,
        "student_answer": student_answer,
        "choice": choice or [student_answer] if student_answer else None,
        "numeric_value": numeric_value,
        "grade_status": grade_status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. Config Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateConfig(unittest.TestCase):

    def setUp(self):
        """Patch config constants so tests run without .env."""
        import config as cfg
        self._orig = cfg.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT
        cfg.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT = 60

    def tearDown(self):
        import config as cfg
        cfg.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT = self._orig

    def _cfg(self, **kwargs):
        from models.mock_state import MockTestConfig, SubjectConfig
        defaults = dict(
            user_id="user-1",
            exam="JEE_ADVANCED",
            subjects=[SubjectConfig(subject="Physics", n_questions=30)],
            total_time_minutes=180,
        )
        defaults.update(kwargs)
        return MockTestConfig(**defaults)

    def test_valid_config_passes(self):
        from services.mock_test import validate_config
        validate_config(self._cfg())  # must not raise

    def test_missing_user_id_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        with self.assertRaises(MockTestConfigError):
            validate_config(self._cfg(user_id=""))

    def test_invalid_exam_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        with self.assertRaises(MockTestConfigError):
            validate_config(self._cfg(exam="JEE_GOD"))

    def test_no_subjects_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        from models.mock_state import MockTestConfig
        cfg = MockTestConfig(user_id="u", exam="JEE_MAIN", subjects=[])
        with self.assertRaises(MockTestConfigError):
            validate_config(cfg)

    def test_invalid_subject_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u",
            exam="JEE_MAIN",
            subjects=[SubjectConfig(subject="Biology", n_questions=10)],
        )
        with self.assertRaises(MockTestConfigError):
            validate_config(cfg)

    def test_duplicate_subject_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u",
            exam="JEE_MAIN",
            subjects=[
                SubjectConfig(subject="Physics", n_questions=10),
                SubjectConfig(subject="Physics", n_questions=10),
            ],
        )
        with self.assertRaises(MockTestConfigError):
            validate_config(cfg)

    def test_zero_n_questions_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u", exam="JEE_ADVANCED",
            subjects=[SubjectConfig(subject="Chemistry", n_questions=0)],
        )
        with self.assertRaises(MockTestConfigError):
            validate_config(cfg)

    def test_n_questions_above_max_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u", exam="JEE_ADVANCED",
            subjects=[SubjectConfig(subject="Physics", n_questions=999)],
        )
        with self.assertRaises(MockTestConfigError):
            validate_config(cfg)

    def test_invalid_difficulty_target_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u", exam="JEE_ADVANCED",
            subjects=[SubjectConfig(subject="Physics", n_questions=10, difficulty_target=9)],
        )
        with self.assertRaises(MockTestConfigError):
            validate_config(cfg)

    def test_custom_marking_scheme_invalid_qtype_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        with self.assertRaises(MockTestConfigError):
            validate_config(self._cfg(marking_scheme={"essay": {"correct": 5}}))

    def test_multi_subject_config_valid(self):
        from services.mock_test import validate_config
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u", exam="JEE_ADVANCED",
            subjects=[
                SubjectConfig(subject="Physics", n_questions=30),
                SubjectConfig(subject="Chemistry", n_questions=25),
                SubjectConfig(subject="Mathematics", n_questions=25),
            ],
        )
        validate_config(cfg)  # must not raise

    def test_time_limit_too_short_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        with self.assertRaises(MockTestConfigError):
            validate_config(self._cfg(total_time_minutes=5))

    def test_time_limit_too_long_raises(self):
        from services.mock_test import MockTestConfigError, validate_config
        with self.assertRaises(MockTestConfigError):
            validate_config(self._cfg(total_time_minutes=1000))


# ══════════════════════════════════════════════════════════════════════════════
# 2. Marking Scheme Resolution
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveMarkingScheme(unittest.TestCase):

    def test_jee_advanced_scheme_loaded(self):
        from services.mock_grading import resolve_marking_scheme
        scheme = resolve_marking_scheme("JEE_ADVANCED")
        self.assertEqual(scheme["single_correct"]["correct"], 3)
        self.assertEqual(scheme["single_correct"]["incorrect"], -1)
        self.assertEqual(scheme["multiple_correct"]["correct"], 4)
        self.assertEqual(scheme["numeric"]["incorrect"], 0)

    def test_jee_main_scheme_loaded(self):
        from services.mock_grading import resolve_marking_scheme
        scheme = resolve_marking_scheme("JEE_MAIN")
        self.assertEqual(scheme["single_correct"]["correct"], 4)
        self.assertEqual(scheme["single_correct"]["incorrect"], -1)
        self.assertEqual(scheme["numeric"]["incorrect"], -1)

    def test_custom_overrides_base(self):
        from services.mock_grading import resolve_marking_scheme
        custom = {"single_correct": {"correct": 5, "incorrect": -2, "unattempted": 0}}
        scheme = resolve_marking_scheme("JEE_ADVANCED", custom)
        self.assertEqual(scheme["single_correct"]["correct"], 5)
        self.assertEqual(scheme["single_correct"]["incorrect"], -2)
        # non-overridden types keep base values
        self.assertEqual(scheme["numeric"]["correct"], 4)

    def test_unknown_exam_falls_back_to_advanced(self):
        from services.mock_grading import resolve_marking_scheme
        scheme = resolve_marking_scheme("SOME_OTHER_EXAM")
        self.assertIn("single_correct", scheme)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Per-question scoring (apply_marking_scheme)
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyMarkingScheme(unittest.TestCase):

    def _scheme(self, exam: str = "JEE_ADVANCED"):
        from services.mock_grading import resolve_marking_scheme
        return resolve_marking_scheme(exam)

    def test_correct_single_scores_positive(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="correct", is_correct=True,
            question_type="single_correct", student_choice=["A"],
            answer={"kind": "letters", "value": "A"}, scheme=self._scheme(),
        )
        self.assertEqual(score, 3.0)

    def test_incorrect_single_scores_negative(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="incorrect", is_correct=False,
            question_type="single_correct", student_choice=["B"],
            answer={"kind": "letters", "value": "A"}, scheme=self._scheme(),
        )
        self.assertEqual(score, -1.0)

    def test_unattempted_scores_zero(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="unattempted", is_correct=None,
            question_type="single_correct", student_choice=None,
            answer=None, scheme=self._scheme(),
        )
        self.assertEqual(score, 0.0)

    def test_numeric_incorrect_jee_advanced_no_penalty(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="incorrect", is_correct=False,
            question_type="numeric", student_choice=None,
            answer={"kind": "integer", "value": 5}, scheme=self._scheme("JEE_ADVANCED"),
        )
        self.assertEqual(score, 0.0)   # JEE Advanced numeric: no negative marking

    def test_numeric_incorrect_jee_main_has_penalty(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="incorrect", is_correct=False,
            question_type="numeric", student_choice=None,
            answer={"kind": "integer", "value": 5}, scheme=self._scheme("JEE_MAIN"),
        )
        self.assertEqual(score, -1.0)  # JEE Main numeric: -1 for wrong

    def test_ungradable_scores_zero(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="ungradable", is_correct=None,
            question_type="single_correct", student_choice=None,
            answer=None, scheme=self._scheme(),
        )
        self.assertEqual(score, 0.0)

    def test_multiple_correct_full_score(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="correct", is_correct=True,
            question_type="multiple_correct", student_choice=["A", "C"],
            answer={"kind": "letters", "value": "AC"}, scheme=self._scheme(),
        )
        self.assertEqual(score, 4.0)

    def test_multiple_correct_wrong_option_is_negative(self):
        from services.mock_grading import apply_marking_scheme
        score = apply_marking_scheme(
            grade_status="incorrect", is_correct=False,
            question_type="multiple_correct", student_choice=["A", "B"],
            answer={"kind": "letters", "value": "AC"}, scheme=self._scheme(),
        )
        self.assertEqual(score, -2.0)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Aggregation
# ══════════════════════════════════════════════════════════════════════════════

class TestAggregateScores(unittest.TestCase):

    def _graded(self, grades):
        """Build minimal graded-submission list from [(grade_status, is_correct, score, qtype)]."""
        return [
            {
                "grade_status": gs,
                "is_correct": ic,
                "question_score": sc,
                "question_type": qt,
                "section": "Physics",
                "chapter_id": "optics",
                "chapter_title": "Optics",
                "concept_id": "refraction",
                "concept_name": "Refraction",
            }
            for gs, ic, sc, qt in grades
        ]

    def _scheme(self):
        from services.mock_grading import resolve_marking_scheme
        return resolve_marking_scheme("JEE_ADVANCED")

    def test_all_correct(self):
        from services.mock_grading import aggregate_scores
        graded = self._graded([
            ("correct", True, 3.0, "single_correct"),
            ("correct", True, 4.0, "numeric"),
        ])
        result = aggregate_scores(graded, self._scheme())
        self.assertEqual(result["questions_correct"], 2)
        self.assertEqual(result["raw_score"], 7.0)
        self.assertEqual(result["accuracy_pct"], 100.0)

    def test_all_unattempted(self):
        from services.mock_grading import aggregate_scores
        graded = self._graded([
            ("unattempted", None, 0.0, "single_correct"),
            ("unattempted", None, 0.0, "numeric"),
        ])
        result = aggregate_scores(graded, self._scheme())
        self.assertEqual(result["questions_attempted"], 0)
        self.assertEqual(result["accuracy_pct"], 0.0)
        self.assertEqual(result["raw_score"], 0.0)

    def test_negative_score_possible(self):
        from services.mock_grading import aggregate_scores
        graded = self._graded([
            ("incorrect", False, -1.0, "single_correct"),
            ("incorrect", False, -1.0, "single_correct"),
        ])
        result = aggregate_scores(graded, self._scheme())
        self.assertLess(result["raw_score"], 0)

    def test_mixed(self):
        from services.mock_grading import aggregate_scores
        graded = self._graded([
            ("correct", True, 3.0, "single_correct"),
            ("incorrect", False, -1.0, "single_correct"),
            ("unattempted", None, 0.0, "single_correct"),
        ])
        result = aggregate_scores(graded, self._scheme())
        self.assertEqual(result["questions_total"], 3)
        self.assertEqual(result["questions_attempted"], 2)
        self.assertEqual(result["questions_correct"], 1)
        self.assertAlmostEqual(result["accuracy_pct"], 50.0, places=1)
        self.assertAlmostEqual(result["raw_score"], 2.0, places=2)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Chapter and Concept Analytics
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalytics(unittest.TestCase):

    def _graded(self):
        return [
            {
                "grade_status": "correct", "is_correct": True,
                "question_score": 3.0, "question_type": "single_correct",
                "section": "Physics", "chapter_id": "optics",
                "chapter_title": "Optics", "concept_id": "refraction",
                "concept_name": "Refraction",
            },
            {
                "grade_status": "incorrect", "is_correct": False,
                "question_score": -1.0, "question_type": "single_correct",
                "section": "Physics", "chapter_id": "optics",
                "chapter_title": "Optics", "concept_id": "refraction",
                "concept_name": "Refraction",
            },
            {
                "grade_status": "correct", "is_correct": True,
                "question_score": 4.0, "question_type": "numeric",
                "section": "Physics", "chapter_id": "laws_of_motion",
                "chapter_title": "Laws of Motion", "concept_id": "newtons_2nd_law",
                "concept_name": "Newton's 2nd Law",
            },
        ]

    def _scheme(self):
        from services.mock_grading import resolve_marking_scheme
        return resolve_marking_scheme("JEE_ADVANCED")

    def test_chapter_analytics_keys(self):
        from services.mock_grading import build_chapter_analytics
        result = build_chapter_analytics(self._graded(), self._scheme())
        self.assertIn("optics", result)
        self.assertIn("laws_of_motion", result)
        self.assertIn("accuracy_pct", result["optics"])
        self.assertIn("chapter_title", result["optics"])

    def test_optics_accuracy_50_percent(self):
        from services.mock_grading import build_chapter_analytics
        result = build_chapter_analytics(self._graded(), self._scheme())
        self.assertAlmostEqual(result["optics"]["accuracy_pct"], 50.0, places=1)

    def test_laws_of_motion_accuracy_100_percent(self):
        from services.mock_grading import build_chapter_analytics
        result = build_chapter_analytics(self._graded(), self._scheme())
        self.assertAlmostEqual(result["laws_of_motion"]["accuracy_pct"], 100.0, places=1)

    def test_concept_analytics_keys(self):
        from services.mock_grading import build_concept_analytics
        result = build_concept_analytics(self._graded())
        self.assertIn("refraction", result)
        self.assertIn("accuracy_pct", result["refraction"])
        self.assertAlmostEqual(result["refraction"]["accuracy_pct"], 50.0, places=1)

    def test_subject_scores_split(self):
        from services.mock_grading import build_subject_scores
        graded = self._graded() + [
            {
                "grade_status": "correct", "is_correct": True,
                "question_score": 3.0, "question_type": "single_correct",
                "section": "Chemistry", "chapter_id": "equilibrium",
                "chapter_title": "Equilibrium", "concept_id": "kp_kc",
                "concept_name": "Kp and Kc",
            }
        ]
        result = build_subject_scores(graded, self._scheme())
        self.assertIn("Physics", result)
        self.assertIn("Chemistry", result)
        self.assertEqual(result["Chemistry"]["questions_correct"], 1)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Feasibility Report Shape
# ══════════════════════════════════════════════════════════════════════════════

class TestFeasibilityReport(unittest.TestCase):

    def test_satisfiable_true_when_enough(self):
        from models.mock_state import FeasibilityReport
        r = FeasibilityReport(requested=30, available=35)
        self.assertTrue(r.satisfiable)

    def test_satisfiable_false_when_short(self):
        from models.mock_state import FeasibilityReport
        r = FeasibilityReport(requested=30, available=20)
        self.assertFalse(r.satisfiable)

    def test_to_dict_shape(self):
        from models.mock_state import FeasibilityReport, FeasibilityGap
        gap = FeasibilityGap(
            chapter_id="solid_state", chapter_title="Solid State",
            subject="Chemistry", requested=5, available=0,
        )
        r = FeasibilityReport(requested=30, available=25, gaps=[gap])
        d = r.to_dict()
        self.assertIn("satisfiable", d)
        self.assertIn("gaps", d)
        self.assertEqual(len(d["gaps"]), 1)
        self.assertEqual(d["gaps"][0]["chapter_id"], "solid_state")

    def test_empty_feasibility(self):
        from models.mock_state import FeasibilityReport
        r = FeasibilityReport(requested=0, available=0)
        self.assertTrue(r.satisfiable)  # 0 of 0 is always satisfiable


# ══════════════════════════════════════════════════════════════════════════════
# 7. MockTestConfig serialisation round-trip
# ══════════════════════════════════════════════════════════════════════════════

class TestMockTestConfigSerialization(unittest.TestCase):

    def test_round_trip(self):
        from models.mock_state import MockTestConfig, SubjectConfig
        original = MockTestConfig(
            user_id="user-abc",
            exam="JEE_MAIN",
            subjects=[
                SubjectConfig(subject="Physics", chapters=["optics"], n_questions=20, difficulty_target=3),
                SubjectConfig(subject="Mathematics", n_questions=25),
            ],
            total_time_minutes=120,
            exclude_seen=True,
        )
        data = original.to_dict()
        restored = MockTestConfig.from_dict(data)
        self.assertEqual(restored.user_id, original.user_id)
        self.assertEqual(restored.exam, original.exam)
        self.assertEqual(restored.total_time_minutes, 120)
        self.assertEqual(restored.subjects[0].chapters, ["optics"])
        self.assertEqual(restored.subjects[0].n_questions, 20)
        self.assertEqual(restored.subjects[1].subject, "Mathematics")
        self.assertTrue(restored.exclude_seen)

    def test_total_questions_property(self):
        from models.mock_state import MockTestConfig, SubjectConfig
        cfg = MockTestConfig(
            user_id="u", exam="JEE_ADVANCED",
            subjects=[
                SubjectConfig(subject="Physics", n_questions=30),
                SubjectConfig(subject="Chemistry", n_questions=25),
                SubjectConfig(subject="Mathematics", n_questions=25),
            ],
        )
        self.assertEqual(cfg.total_questions, 80)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Debrief analytics helpers (offline — no LLM or DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestDebriefHelpers(unittest.TestCase):

    def _graded_questions(self):
        return [
            {"concept_id": "refraction", "concept_name": "Refraction",
             "is_correct": True,  "grade_status": "correct",   "section": "Physics"},
            {"concept_id": "refraction", "concept_name": "Refraction",
             "is_correct": False, "grade_status": "incorrect",  "section": "Physics"},
            {"concept_id": "newtons_2nd", "concept_name": "Newton's 2nd Law",
             "is_correct": False, "grade_status": "incorrect",  "section": "Physics"},
            {"concept_id": "newtons_2nd", "concept_name": "Newton's 2nd Law",
             "is_correct": False, "grade_status": "incorrect",  "section": "Physics"},
            {"concept_id": "kp_kc",     "concept_name": "Kp and Kc",
             "is_correct": True,  "grade_status": "correct",   "section": "Chemistry"},
        ]

    def test_follow_up_returns_weakest_concepts(self):
        from agents.mock_debrief import _follow_up_concepts
        concepts = _follow_up_concepts(self._graded_questions(), limit=3)
        # newtons_2nd has 0% accuracy — should be first
        self.assertEqual(concepts[0], "newtons_2nd")
        self.assertIn("refraction", concepts)

    def test_weak_concept_list_format(self):
        from agents.mock_debrief import _weak_concept_list
        text = _weak_concept_list(self._graded_questions())
        self.assertIn("Newton's 2nd Law", text)
        # refraction is 50% — below 50% threshold? No, 50% is not < 50%
        # so refraction should not appear; only newtons_2nd (0%) appears
        self.assertNotIn("Refraction", text)

    def test_strong_chapters_list(self):
        from agents.mock_debrief import _strong_chapters_list
        chapter_scores = {
            "optics": {"chapter_title": "Optics", "accuracy_pct": 80, "questions_attempted": 5},
            "mechanics": {"chapter_title": "Mechanics", "accuracy_pct": 30, "questions_attempted": 5},
        }
        result = _strong_chapters_list(chapter_scores)
        self.assertIn("Optics", result)
        self.assertNotIn("Mechanics", result)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Integration stub — full pipeline with mocked DB / retrieval
# ══════════════════════════════════════════════════════════════════════════════

class TestMockTestIntegration(unittest.TestCase):
    """End-to-end flow through services/mock_test.py using patched DB and retrieval.

    This does NOT hit the real database or the retrieval Postgres. It verifies
    that the pipeline wires together correctly and produces the right shape of
    output for a minimal two-question test.
    """

    def setUp(self):
        import config as cfg
        cfg.MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT = 60
        cfg.EXAM_ALLOWED_DB = {
            "JEE_ADVANCED": ("Jee Advanced", "Jee main and Advanced"),
            "JEE_MAIN": ("Jee main", "Jee main and Advanced"),
            "custom": (),
        }

    def _make_chunk(self, cid: str = "chunk_1") -> Dict[str, Any]:
        """Minimal search hit chunk with a letters answer."""
        class FakeHit:
            def as_chunk(self):
                return {
                    "content_id": cid,
                    "content_type": "PYQ",
                    "question_type": "single_correct",
                    "difficulty": 3,
                    "subject": "Physics",
                    "concept_id": "phy_optics_refraction",
                    "primary_concept_id": "phy_optics_refraction",
                    "core_text": "What is the refractive index?",
                    "structured": json.dumps({
                        "stem": "What is the refractive index?",
                        "options": [
                            {"label": "A", "text": "1.0"},
                            {"label": "B", "text": "1.5"},
                        ],
                        "answer": {"kind": "letters", "value": "B"},
                    }),
                    "answer": json.dumps({"kind": "letters", "value": "B"}),
                    "has_solution": 1,
                    "fingerprint": f"fp_{cid}",
                    "token_len": 50,
                    "solution": "",
                }
        return FakeHit()

    @patch("services.mock_test.store.chapters")
    @patch("services.mock_test.store.all_concepts")
    @patch("services.mock_test.store.concept")
    @patch("services.mock_test.store.query")
    @patch("services.mock_test.search.search")
    @patch("services.mock_test.db")
    def test_assemble_test_shape(
        self,
        mock_db,
        mock_search,
        mock_store_query,
        mock_concept,
        mock_all_concepts,
        mock_chapters,
    ):
        from models.mock_state import MockTestConfig, SubjectConfig
        from services.mock_test import assemble_test

        # Stub retrieval store
        mock_chapters.return_value = [
            {"chapter_id": "optics", "title": "Optics", "subject": "Physics", "in_syllabus": True}
        ]
        mock_all_concepts.return_value = [
            {"concept_id": "phy_optics_refraction", "chapter_id": "optics", "name": "Refraction", "subject": "Physics"}
        ]
        mock_concept.return_value = {"concept_id": "phy_optics_refraction", "name": "Refraction"}
        mock_store_query.return_value = [{"n": 10}]   # inventory count

        # Stub search to return two distinct chunks
        class FakeResult:
            hits = [self._make_chunk("q_a"), self._make_chunk("q_b")]
        mock_search.return_value = FakeResult()

        # Stub DB writes
        mock_db.client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        cfg = MockTestConfig(
            user_id="user-test",
            exam="JEE_ADVANCED",
            subjects=[SubjectConfig(subject="Physics", chapters=["optics"], n_questions=2)],
            total_time_minutes=60,
        )

        result = assemble_test(cfg)

        self.assertIn("mock_test_id", result)
        self.assertEqual(result["status"], "built")
        self.assertIn("sections", result)
        self.assertEqual(len(result["sections"]), 1)
        section = result["sections"][0]
        self.assertEqual(section["subject"], "Physics")
        # At least some questions were selected (up to 2)
        self.assertGreater(len(section["questions"]), 0)
        # Answers are NOT in student-facing question dicts
        for q in section["questions"]:
            self.assertNotIn("answer", q)

    def test_grade_submission_correct_mcq(self):
        """grade_submission returns correct when student chooses the right option."""
        from services.mock_grading import grade_submission, resolve_marking_scheme

        question = _q(
            content_id="q1",
            question_type="single_correct",
            answer={"kind": "letters", "letters": "B"},
        )
        question["answer"] = json.dumps({"kind": "letters", "letters": "B"})
        submission = _sub(content_id="q1", student_answer="B", choice=["B"])
        scheme = resolve_marking_scheme("JEE_ADVANCED")

        result = grade_submission(question_row=question, submission=submission, scheme=scheme)
        self.assertEqual(result["grade_status"], "correct")
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["question_score"], 3.0)

    def test_grade_submission_incorrect_mcq(self):
        from services.mock_grading import grade_submission, resolve_marking_scheme

        question = _q(
            content_id="q1",
            question_type="single_correct",
            answer={"kind": "letters", "letters": "A"},
        )
        question["answer"] = json.dumps({"kind": "letters", "letters": "A"})
        submission = _sub(content_id="q1", student_answer="C", choice=["C"])
        scheme = resolve_marking_scheme("JEE_ADVANCED")

        result = grade_submission(question_row=question, submission=submission, scheme=scheme)
        self.assertEqual(result["grade_status"], "incorrect")
        self.assertFalse(result["is_correct"])
        self.assertEqual(result["question_score"], -1.0)

    def test_grade_submission_unattempted(self):
        from services.mock_grading import grade_submission, resolve_marking_scheme

        question = _q(content_id="q1", question_type="single_correct")
        scheme = resolve_marking_scheme("JEE_ADVANCED")
        result = grade_submission(question_row=question, submission=None, scheme=scheme)
        self.assertEqual(result["grade_status"], "unattempted")
        self.assertEqual(result["question_score"], 0.0)

    def test_grade_submission_no_gradekey_is_ungradable(self):
        from services.mock_grading import grade_submission, resolve_marking_scheme

        question = _q(content_id="q1", has_gradekey=False)
        question["answer"] = None
        submission = _sub(content_id="q1", student_answer="A", choice=["A"])
        scheme = resolve_marking_scheme("JEE_ADVANCED")
        result = grade_submission(question_row=question, submission=submission, scheme=scheme)
        self.assertEqual(result["grade_status"], "ungradable")
        self.assertEqual(result["question_score"], 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# 10. Edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):

    def test_config_from_dict_missing_subjects_returns_empty(self):
        from models.mock_state import MockTestConfig
        cfg = MockTestConfig.from_dict({"user_id": "u", "exam": "JEE_MAIN"})
        self.assertEqual(cfg.subjects, [])

    def test_qtype_normalised_handles_uppercase(self):
        from services.mock_grading import _qtype_normalised
        self.assertEqual(_qtype_normalised("SINGLE_CORRECT"), "single_correct")
        self.assertEqual(_qtype_normalised("MCQ"), "single_correct")
        self.assertEqual(_qtype_normalised("NUMERIC"), "numeric")

    def test_max_score_for_scheme(self):
        from services.mock_grading import max_score_for_scheme, resolve_marking_scheme
        scheme = resolve_marking_scheme("JEE_ADVANCED")
        self.assertEqual(max_score_for_scheme(scheme, "single_correct"), 3.0)
        self.assertEqual(max_score_for_scheme(scheme, "numeric"), 4.0)

    def test_feasibility_gap_to_dict(self):
        from models.mock_state import FeasibilityGap
        gap = FeasibilityGap(
            chapter_id="ch1", chapter_title="Chapter 1",
            subject="Physics", requested=10, available=3,
        )
        d = gap.to_dict()
        self.assertEqual(d["requested"], 10)
        self.assertEqual(d["available"], 3)
        self.assertEqual(d["chapter_id"], "ch1")

    def test_aggregate_empty_graded(self):
        from services.mock_grading import aggregate_scores, resolve_marking_scheme
        result = aggregate_scores([], resolve_marking_scheme("JEE_ADVANCED"))
        self.assertEqual(result["questions_total"], 0)
        self.assertEqual(result["raw_score"], 0.0)
        self.assertEqual(result["accuracy_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
