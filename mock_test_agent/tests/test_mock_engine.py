"""Selection, blueprints, and scoring rules for the three mock modes.

These tests do not open a database. They pass question rows straight into the
engine, which is the same function the build route uses after retrieval.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any, Dict, List

from mock_test_agent.blueprints import organizing_iit
from mock_test_agent.engine import MockBuildError, compose
from mock_test_agent.learner import fingerprint, is_learned, learned_index
from mock_test_agent.models import MockTestConfig, SubjectConfig
from mock_test_agent.pool import is_genuine_official
from mock_test_agent.selector import redistribute


def _row(
    cid: str,
    *,
    concept: str,
    subject: str = "Physics",
    chapter: str = "laws",
    qtype: str = "single_correct",
    difficulty: int = 3,
    content_type: str = "Practice Question",
    source_book: str = "NCERT",
    license_: str = "ncert",
    secondary: str = "",
) -> Dict[str, Any]:
    options = None if qtype in ("numeric", "integer") else [
        {"label": "A", "text": "1"}, {"label": "B", "text": "2"},
    ]
    return {
        "content_id": cid,
        "concept_id": concept,
        "secondary_concepts": secondary,
        "question_type": qtype,
        "content_type": content_type,
        "subject": subject,
        "chapter_id": chapter,
        "chapter_title": chapter,
        "difficulty": difficulty,
        "stem": f"Question {cid}",
        "options": options,
        "answer": {"kind": "integer", "value": 1} if qtype in ("numeric", "integer") else {"kind": "letters", "letters": "A"},
        "fingerprint": f"fp-{cid}",
        "source_book": source_book,
        "source_ref": "Q1",
        "license": license_,
        "concept_name": concept,
    }


def _learned(concept: str, score: float, subject: str = "Physics", attempts: int = 3) -> Dict[str, Any]:
    return {
        "concept_id": concept,
        "proficiency_score": score,
        "is_cold_start": False,
        "attempts": attempts,
        "subject": subject,
    }


def _cfg(**kwargs: Any) -> MockTestConfig:
    subjects = kwargs.pop("subjects")
    return MockTestConfig(user_id="student-1", subjects=subjects, total_time_minutes=60, **kwargs)


class TestLearner(unittest.TestCase):
    def test_unlearned_is_not_weak(self):
        rows = [
            {"concept_id": "new", "proficiency_score": 0.1, "is_cold_start": True, "attempts": 0},
            _learned("friction", 0.3),
        ]
        index = learned_index(rows)
        self.assertNotIn("new", index)
        self.assertEqual(index["friction"]["band"], "weak")
        self.assertFalse(is_learned(rows[0]))

    def test_fingerprint_changes_when_proficiency_changes(self):
        first = learned_index([_learned("friction", 0.30)])
        second = learned_index([_learned("friction", 0.80)])
        self.assertNotEqual(fingerprint(first), fingerprint(second))


class TestSmartAndCustom(unittest.TestCase):
    def test_smart_uses_only_learned_concepts_and_every_source(self):
        rows = [
            _row("p1", concept="friction", content_type="Practice Question"),
            _row("e1", concept="friction", content_type="Solved Example"),
            _row("y1", concept="friction", content_type="PYQ", source_book="Coaching test paper"),
            _row("other", concept="torque", content_type="Practice Question"),
        ]
        cfg = _cfg(
            exam="JEE_MAIN", mock_type="smart", coverage="learned",
            subjects=[SubjectConfig(subject="Physics", n_questions=3)],
        )
        paper = compose(cfg, rows, [_learned("friction", 0.3)])
        ids = {q.content_id for q in paper["questions"]}
        self.assertIn("p1", ids)
        self.assertIn("e1", ids)
        self.assertIn("y1", ids)
        self.assertNotIn("other", ids)
        self.assertTrue(all("answer" not in q.to_dict() for q in paper["questions"]))

    def test_smart_still_includes_a_subject_the_student_has_not_learned(self):
        cfg = _cfg(exam="JEE_MAIN", mock_type="smart", subjects=[
            SubjectConfig(subject="Physics", n_questions=1),
            SubjectConfig(subject="Chemistry", n_questions=1),
        ])
        paper = compose(
            cfg,
            [_row("phy", concept="friction"), _row("chem", concept="mole", subject="Chemistry", chapter="mole")],
            [_learned("friction", 0.4)],
        )
        self.assertEqual({q.section for q in paper["questions"]}, {"Physics", "Chemistry"})
        self.assertTrue(paper["meta"]["contains_unlearned"])

    def test_weak_concepts_receive_more_questions_than_strong_ones(self):
        rows = []
        for i in range(8):
            rows.append(_row(f"w{i}", concept="friction", difficulty=2))
            rows.append(_row(f"s{i}", concept="newton", difficulty=4))
        cfg = _cfg(exam="JEE_ADVANCED", mock_type="smart", subjects=[SubjectConfig(subject="Physics", n_questions=10)])
        paper = compose(cfg, rows, [_learned("friction", 0.2), _learned("newton", 0.9)])
        weak = sum(1 for q in paper["questions"] if q.concept_id == "friction")
        strong = sum(1 for q in paper["questions"] if q.concept_id == "newton")
        self.assertGreater(weak, strong)
        self.assertEqual(paper["meta"]["allocation"]["weak"] + paper["meta"]["allocation"]["strong"], 10)

    def test_empty_band_is_redistributed(self):
        self.assertEqual(redistribute(10, {"weak": 0.5, "medium": 0.3, "strong": 0.2}, ["strong"]), {"strong": 10})

    def test_multi_concept_question_needs_every_concept_learned(self):
        rows = [_row("mix", concept="friction", secondary="torque")]
        cfg = _cfg(exam="JEE_MAIN", mock_type="smart", subjects=[SubjectConfig(subject="Physics", n_questions=1)])
        with self.assertRaises(MockBuildError):
            compose(cfg, rows, [_learned("friction", 0.4)])
        paper = compose(cfg, rows, [_learned("friction", 0.4), _learned("torque", 0.8)])
        self.assertEqual(paper["questions"][0].content_id, "mix")

    def test_custom_one_chapter_and_several_chapters(self):
        rows = [
            _row("a", concept="friction", chapter="laws"),
            _row("b", concept="work", chapter="energy"),
        ]
        one = compose(
            _cfg(exam="JEE_MAIN", mock_type="custom", coverage="all",
                 subjects=[SubjectConfig(subject="Physics", chapters=["laws"], n_questions=1)]),
            rows, [],
        )
        self.assertEqual([q.content_id for q in one["questions"]], ["a"])
        both = compose(
            _cfg(exam="JEE_MAIN", mock_type="custom", coverage="all",
                 subjects=[SubjectConfig(subject="Physics", chapters=["laws", "energy"], n_questions=2)]),
            rows, [],
        )
        self.assertEqual({q.chapter_id for q in both["questions"]}, {"laws", "energy"})

    def test_learned_only_versus_explicit_unlearned(self):
        rows = [
            _row("known", concept="friction", chapter="laws"),
            _row("new", concept="torque", chapter="laws"),
        ]
        learned = [_learned("friction", 0.4)]
        cfg_kwargs = dict(exam="JEE_ADVANCED", mock_type="custom",
                          subjects=[SubjectConfig(subject="Physics", chapters=["laws"], n_questions=2)])
        only = compose(_cfg(coverage="learned", **cfg_kwargs), rows, learned)
        self.assertEqual([q.content_id for q in only["questions"]], ["known"])
        whole = compose(_cfg(coverage="all", **cfg_kwargs), rows, learned)
        self.assertTrue(whole["meta"]["contains_unlearned"])
        self.assertIn("new", {q.content_id for q in whole["questions"]})

    def test_subject_selection_does_not_pull_other_subjects(self):
        rows = [
            _row("phy", concept="friction", subject="Physics"),
            _row("chem", concept="mole", subject="Chemistry"),
            _row("math", concept="limits", subject="Mathematics"),
        ]
        learner = [_learned("friction", 0.4), _learned("mole", 0.4, "Chemistry"), _learned("limits", 0.4, "Mathematics")]
        physics = compose(
            _cfg(exam="JEE_MAIN", mock_type="smart", subjects=[SubjectConfig(subject="Physics", n_questions=1)]),
            rows, learner,
        )
        self.assertEqual({q.section for q in physics["questions"]}, {"Physics"})
        self.assertTrue(physics["meta"]["sectional"])
        pair = compose(
            _cfg(exam="JEE_MAIN", mock_type="smart", subjects=[
                SubjectConfig(subject="Chemistry", n_questions=1),
                SubjectConfig(subject="Mathematics", n_questions=1),
            ]),
            rows, learner,
        )
        self.assertEqual({q.section for q in pair["questions"]}, {"Chemistry", "Mathematics"})

    def test_sections_are_not_jumbled_and_carry_instructions(self):
        rows = [
            _row("n1", concept="friction", qtype="integer"),
            _row("s1", concept="friction", qtype="single_correct"),
        ]
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="custom", coverage="all",
                 subjects=[SubjectConfig(subject="Physics", chapters=["laws"], n_questions=2)]),
            rows, [],
        )
        sections = [q.paper_section for q in paper["questions"]]
        self.assertLess(sections.index(next(s for s in sections if "Section A" in s)),
                        sections.index(next(s for s in sections if "Section B" in s)))
        self.assertTrue(all(q.instructions for q in paper["questions"]))
        self.assertNotIn("answer", paper["questions"][0].to_dict())

    def test_seen_questions_are_used_only_after_fresh_ones(self):
        rows = [_row("old", concept="friction"), _row("new", concept="friction")]
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="smart", subjects=[SubjectConfig(subject="Physics", n_questions=1)]),
            rows, [_learned("friction", 0.3)], seen={"old"},
        )
        self.assertEqual(paper["questions"][0].content_id, "new")

    def test_duplicate_fingerprint_is_dropped(self):
        a = _row("a", concept="friction")
        b = _row("b", concept="friction")
        b["fingerprint"] = a["fingerprint"]
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="custom", coverage="all",
                 subjects=[SubjectConfig(subject="Physics", chapters=["laws"], n_questions=2)]),
            [a, b], [],
        )
        self.assertEqual(len(paper["questions"]), 1)


class TestPyqAndBlueprints(unittest.TestCase):
    def _official(self, cid: str, year: int, paper: int, subject: str = "Physics", qno: int = 1, **kwargs: Any) -> Dict[str, Any]:
        qtype = kwargs.pop("qtype", "single_correct")
        chapter = kwargs.pop("chapter", "rotation")
        concept = kwargs.pop("concept", "torque")
        row = _row(
            cid, concept=concept, subject=subject, chapter=chapter, qtype=qtype, content_type="PYQ",
            source_book=f"JEE Advanced {year} Paper {paper}",
            license_="official exam paper (jeeadv.ac.in) with official final answer key",
        )
        row["source_ref"] = f"Q{qno}"
        row["stem"] = kwargs.pop("stem", f"Question {cid} from {year}")
        return row

    def _main(self, cid: str, year: int, qtype: str, subject: str = "Physics", chapter: str = "laws", concept: str = "friction") -> Dict[str, Any]:
        row = _row(
            cid, concept=concept, subject=subject, chapter=chapter, qtype=qtype, content_type="PYQ",
            source_book=f"JEE Official Exam (JEE_Main_{year}_April_Shift_1)",
            license_="official exam paper",
        )
        row["source_ref"] = f"Q{cid}"
        row["stem"] = f"Main question {cid} {year}"
        return row

    def _main_bank(self, subject: str = "Physics", chapter: str = "laws", concept: str = "friction", years: tuple = (2021, 2024, 2026)) -> List[Dict[str, Any]]:
        rows = []
        for i in range(20):
            rows.append(self._main(f"{subject[0]}s{i}", years[i % len(years)], "single_correct", subject, chapter, concept))
        for i in range(5):
            rows.append(self._main(f"{subject[0]}n{i}", years[i % len(years)], "integer", subject, chapter, concept))
        return rows

    def _advanced_bank(self, subject: str = "Physics", chapter: str = "rotation", concept: str = "torque") -> List[Dict[str, Any]]:
        years = (2014, 2017, 2019, 2022, 2024)
        rows = []
        for i in range(4):
            rows.append(self._official(f"{subject[0]}s{i}", years[i], 1, subject, i + 1, qtype="single_correct", chapter=chapter, concept=concept))
        for i in range(3):
            rows.append(self._official(f"{subject[0]}m{i}", years[i], 2, subject, i + 1, qtype="multiple_correct", chapter=chapter, concept=concept))
        for i in range(6):
            rows.append(self._official(f"{subject[0]}i{i}", years[i % 5], 1, subject, 10 + i, qtype="integer", chapter=chapter, concept=concept))
        for i in range(4):
            rows.append(self._official(f"{subject[0]}k{i}", years[i], 1, subject, 20 + i, qtype="matching", chapter=chapter, concept=concept))
        return rows

    def test_pyq_rejects_practice_coaching_and_examples(self):
        official = self._official("off", 2024, 1)
        practice = _row("prac", concept="torque", content_type="Practice Question", chapter="rotation")
        example = _row("ex", concept="torque", content_type="Solved Example", chapter="rotation")
        coaching = _row("coach", concept="torque", content_type="PYQ", source_book="Coaching test paper 1", license_="coaching", chapter="rotation")
        self.assertTrue(is_genuine_official(official))
        self.assertFalse(is_genuine_official(coaching))
        with self.assertRaises(MockBuildError) as caught:
            compose(
                _cfg(exam="JEE_ADVANCED", mock_type="pyq", pyq_length="full", pyq_seed=1,
                     subjects=[SubjectConfig(subject="Physics", chapters=["rotation"], n_questions=0)]),
                [official, practice, example, coaching], [],
            )
        self.assertIn("full mock cannot be generated", str(caught.exception).lower())
        self.assertNotIn("10", str(caught.exception))

    def test_main_full_three_subjects_follow_the_2025_blueprint(self):
        rows = []
        for subject, chapter in (("Physics", "laws"), ("Chemistry", "mole"), ("Mathematics", "algebra")):
            rows.extend(self._main_bank(subject, chapter, concept=chapter))
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="pyq", pyq_length="full", pyq_seed=2,
                 subjects=[SubjectConfig(subject=s, n_questions=0) for s in ("Physics", "Chemistry", "Mathematics")]),
            rows, [],
        )
        questions = paper["questions"]
        self.assertEqual(len(questions), 75)
        self.assertEqual(paper["meta"]["max_marks"], 300)
        self.assertEqual(paper["meta"]["time_limit_minutes"], 180)
        self.assertEqual(paper["meta"]["blueprint_id"], "jee_main_2025")
        self.assertFalse(paper["meta"]["sectional"])
        self.assertTrue(paper["meta"]["genuine_pyq"])
        for subject in ("Physics", "Chemistry", "Mathematics"):
            group = [q for q in questions if q.section == subject]
            self.assertEqual(len(group), 25)
            section_a = [q for q in group if "Section A" in q.paper_section]
            section_b = [q for q in group if "Section B" in q.paper_section]
            self.assertEqual(len(section_a), 20)
            self.assertTrue(all(q.question_type == "single_correct" for q in section_a))
            self.assertEqual(len(section_b), 5)
            self.assertTrue(all(q.question_type in ("numeric", "integer") for q in section_b))
            self.assertTrue(all(q._marks["correct"] == 4 and q._marks["incorrect"] == -1 for q in group))
        years = set(paper["meta"]["source_years"])
        self.assertGreater(len(years), 1)
        self.assertTrue(all(q.source_type == "PYQ" for q in questions))
        self.assertNotIn("answer", questions[0].to_dict())

    def test_main_physics_only_is_a_sectional_paper(self):
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="pyq", pyq_length="full", pyq_seed=3,
                 subjects=[SubjectConfig(subject="Physics", chapters=["laws"], n_questions=0)]),
            self._main_bank(), [],
        )
        self.assertEqual(len(paper["questions"]), 25)
        self.assertEqual(paper["meta"]["max_marks"], 100)
        self.assertEqual(paper["meta"]["time_limit_minutes"], 60)
        self.assertTrue(paper["meta"]["sectional"])
        self.assertIn("sectional", paper["meta"]["length_label"].lower())
        self.assertNotIn("Chemistry", {q.section for q in paper["questions"]})

    def test_advanced_full_section_structure_and_mixed_years(self):
        rows = self._advanced_bank("Physics") + self._advanced_bank("Mathematics", "algebra", "limits")
        paper = compose(
            _cfg(exam="JEE_ADVANCED", mock_type="pyq", pyq_length="full", pyq_seed=4,
                 subjects=[
                     SubjectConfig(subject="Physics", chapters=["rotation"], concepts=["torque"], n_questions=0),
                     SubjectConfig(subject="Mathematics", chapters=["algebra"], concepts=["limits"], n_questions=0),
                 ]),
            rows, [],
        )
        self.assertEqual(len(paper["questions"]), 34)
        self.assertEqual(paper["meta"]["blueprint_id"], "jee_advanced_2024_paper1")
        self.assertEqual(paper["meta"]["max_marks"], 120)
        self.assertTrue(paper["meta"]["sectional"])
        physics = [q for q in paper["questions"] if q.section == "Physics"]
        self.assertEqual(len(physics), 17)
        counts = {}
        for q in physics:
            counts[q.question_type] = counts.get(q.question_type, 0) + 1
        self.assertEqual(counts["single_correct"], 4)
        self.assertEqual(counts["multiple_correct"], 3)
        self.assertEqual(counts["integer"], 6)
        self.assertEqual(counts["matching"], 4)
        self.assertGreater(len(set(paper["meta"]["source_years"])), 1)
        self.assertTrue(all(q.chapter_id == "rotation" for q in physics))
        multi = next(q for q in physics if q.question_type == "multiple_correct")
        self.assertEqual(multi._marks["correct"], 4)
        self.assertEqual(multi._marks["partial"], 1)
        self.assertEqual(multi._marks["incorrect"], -2)
        single = next(q for q in physics if q.question_type == "single_correct")
        self.assertEqual(single._marks["incorrect"], -1)
        self.assertNotEqual(single._marks["correct"], 4)

    def test_matching_lists_saved_as_mcqs_fill_the_matching_section(self):
        rows = []
        for i in range(4):
            rows.append(self._official(f"plain{i}", 2018, 1, qno=i + 1, qtype="single_correct"))
        for i in range(4):
            row = self._official(f"list{i}", 2024, 1, qno=30 + i, qtype="single_correct")
            row["stem"] = f"Match List-I with List-II for item {i}."
            rows.append(row)
        for i in range(3):
            rows.append(self._official(f"multi{i}", 2019, 2, qno=i + 1, qtype="multiple_correct"))
        for i in range(6):
            rows.append(self._official(f"int{i}", 2021, 1, qno=10 + i, qtype="integer"))
        paper = compose(
            _cfg(exam="JEE_ADVANCED", mock_type="pyq", pyq_length="full", pyq_seed=11,
                 subjects=[SubjectConfig(subject="Physics", chapters=["rotation"], n_questions=0)]),
            rows, [],
        )
        matching = [q for q in paper["questions"] if "Matching" in q.paper_section]
        self.assertEqual({q.content_id for q in matching}, {f"list{i}" for i in range(4)})
        self.assertTrue(all(q.question_type == "single_correct" for q in matching))
        self.assertTrue(all(q._marks["correct"] == 3 and q._marks["incorrect"] == -1 for q in matching))
        singles = [q for q in paper["questions"] if "Single correct" in q.paper_section]
        self.assertEqual({q.content_id for q in singles}, {f"plain{i}" for i in range(4)})

    def test_selected_chapters_are_respected(self):
        rows = self._advanced_bank("Physics", "rotation", "torque")
        rows.append(self._official("electro", 2018, 1, chapter="electrostatics", concept="coulomb", qno=3))
        paper = compose(
            _cfg(exam="JEE_ADVANCED", mock_type="pyq", pyq_length="full", pyq_seed=5,
                 subjects=[SubjectConfig(subject="Physics", chapters=["rotation"], n_questions=0)]),
            rows, [],
        )
        self.assertTrue(all(q.chapter_id == "rotation" for q in paper["questions"]))
        self.assertNotIn("electro", {q.content_id for q in paper["questions"]})

    def test_duplicates_and_invalid_rows_are_excluded(self):
        rows = self._main_bank()
        twin = dict(rows[0])
        twin["content_id"] = "twin"
        twin["fingerprint"] = rows[0]["fingerprint"]
        broken = dict(rows[1])
        broken["content_id"] = "broken"
        broken["fingerprint"] = "fp-broken"
        broken["answer"] = {}
        broken["stem"] = "Broken stem that is unique"
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="pyq", pyq_length="full", pyq_seed=6,
                 subjects=[SubjectConfig(subject="Physics", n_questions=0)]),
            rows + [twin, broken], [],
        )
        ids = [q.content_id for q in paper["questions"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse({"twin", rows[0]["content_id"]} <= set(ids))
        self.assertNotIn("broken", ids)

    def test_full_mock_is_never_silently_truncated(self):
        short_bank = [self._main(f"s{i}", 2024, "single_correct") for i in range(10)]
        with self.assertRaises(MockBuildError) as caught:
            compose(
                _cfg(exam="JEE_MAIN", mock_type="pyq", pyq_length="full", pyq_seed=7,
                     subjects=[SubjectConfig(subject="Physics", n_questions=0)]),
                short_bank, [],
            )
        message = str(caught.exception)
        self.assertIn("Only 10 valid PYQs", message)
        self.assertIn("full mock cannot be generated", message.lower())
        self.assertIn("shortened mock", message.lower())

    def test_legacy_iit_jee_is_not_labelled_advanced(self):
        from mock_test_agent.pool import source_of
        legacy = source_of("JEE Advanced 2012 Paper 1", "official exam paper")
        self.assertEqual(legacy["exam"], "IIT_JEE")
        self.assertNotIn("JEE Advanced", legacy["label"])
        named = source_of("IIT-JEE 2011", "official")
        self.assertEqual(named["exam"], "IIT_JEE")
        modern = source_of("JEE Advanced 2014 Paper 2", "official exam paper")
        self.assertEqual(modern["exam"], "JEE_ADVANCED")

    def test_main_does_not_use_advanced_questions(self):
        rows = self._main_bank() + self._advanced_bank()
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="pyq", pyq_length="full", pyq_seed=8,
                 subjects=[SubjectConfig(subject="Physics", n_questions=0)]),
            rows, [],
        )
        self.assertTrue(all(getattr(q, "_source_exam", "") == "JEE_MAIN" for q in paper["questions"]))

    def test_seen_questions_wait_behind_fresh_ones(self):
        rows = self._main_bank() + [self._main("extra", 2022, "single_correct")]
        seen = {rows[0]["content_id"]}
        paper = compose(
            _cfg(exam="JEE_MAIN", mock_type="pyq", pyq_length="full", pyq_seed=9,
                 subjects=[SubjectConfig(subject="Physics", n_questions=0)]),
            rows, [], seen=seen,
        )
        self.assertNotIn(rows[0]["content_id"], [q.content_id for q in paper["questions"]])

    def test_organizing_iit_is_only_the_verified_registry(self):
        self.assertEqual(organizing_iit(2014)["iit"], "IIT Kharagpur")
        self.assertEqual(organizing_iit(2014)["status"], "verified")
        self.assertEqual(organizing_iit(2017)["iit"], "IIT Madras")
        self.assertEqual(organizing_iit(2024)["status"], "verified")
        unknown = organizing_iit(2016)
        self.assertEqual(unknown["status"], "unconfirmed")
        self.assertEqual(unknown["iit"], "")
        self.assertNotIn("Bombay", unknown["iit"])

    def test_main_and_advanced_marking_differ(self):
        from mock_test_agent.grading import apply_marking_scheme, resolve_marking_scheme
        main = resolve_marking_scheme("JEE_MAIN")
        advanced = resolve_marking_scheme("JEE_ADVANCED")
        self.assertEqual(apply_marking_scheme(
            grade_status="incorrect", is_correct=False, question_type="numeric",
            student_choice=None, answer=None, scheme=main,
        ), -1.0)
        self.assertEqual(apply_marking_scheme(
            grade_status="incorrect", is_correct=False, question_type="numeric",
            student_choice=None, answer=None, scheme=advanced,
        ), 0.0)
        self.assertEqual(apply_marking_scheme(
            grade_status="incorrect", is_correct=False, question_type="multiple_correct",
            student_choice=["A"], answer={"kind": "letters", "letters": "AB"}, scheme=advanced,
        ), 1.0)
        self.assertEqual(apply_marking_scheme(
            grade_status="incorrect", is_correct=False, question_type="multiple_correct",
            student_choice=["A", "B"], answer={"kind": "letters", "letters": "ABC"}, scheme=advanced,
        ), 2.0)
        self.assertEqual(apply_marking_scheme(
            grade_status="incorrect", is_correct=False, question_type="multiple_correct",
            student_choice=["A", "B", "C"], answer={"kind": "letters", "letters": "ABCD"}, scheme=advanced,
        ), 3.0)
        self.assertEqual(apply_marking_scheme(
            grade_status="incorrect", is_correct=False, question_type="multiple_correct",
            student_choice=["A", "D"], answer={"kind": "letters", "letters": "ABC"}, scheme=advanced,
        ), -2.0)
        from mock_test_agent.grading import prepare_numeric_text
        self.assertEqual(prepare_numeric_text(
            {"numeric_mode": "nearest_integer"}, "2.4", 2.4,
        ), "2")
        self.assertEqual(prepare_numeric_text(
            {"numeric_mode": "nearest_integer"}, "2.5", None,
        ), "3")
        self.assertEqual(prepare_numeric_text(
            {"numeric_mode": "integer"}, "2.4", 2.4,
        ), "2.4")


class TestProficiencyWrites(unittest.TestCase):
    def test_one_write_per_concept_and_seen_questions_do_not_move_proficiency(self):
        from mock_test_agent.grading import grade_all_submissions
        calls: List[str] = []

        def _commit(**kwargs: Any):
            calls.append(kwargs["concept_id"])
            class Result:
                attempt_id = "att"
            return Result()

        questions = [
            {"content_id": "a", "concept_id": "friction", "question_type": "single_correct",
             "has_gradekey": True, "answer": {"kind": "letters", "letters": "A"}, "options": [],
             "previously_seen": False, "difficulty": 3},
            {"content_id": "b", "concept_id": "friction", "question_type": "single_correct",
             "has_gradekey": True, "answer": {"kind": "letters", "letters": "A"}, "options": [],
             "previously_seen": False, "difficulty": 4},
            {"content_id": "c", "concept_id": "torque", "question_type": "single_correct",
             "has_gradekey": True, "answer": {"kind": "letters", "letters": "A"}, "options": [],
             "previously_seen": True, "difficulty": 3},
        ]
        submissions = [
            {"content_id": cid, "choice": ["A"], "student_answer": "A"} for cid in ("a", "b", "c")
        ]
        with patch("services.grading.commit_progress", _commit):
            grade_all_submissions(
                mock_test_id="m1", user_id="student-1", session_id="s",
                questions=questions, submissions=submissions,
                scheme={"single_correct": {"correct": 4, "incorrect": -1, "unattempted": 0}},
            )
        self.assertEqual(calls, ["friction"])


if __name__ == "__main__":
    unittest.main()
