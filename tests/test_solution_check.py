"""retrieval.solution_check: reads the body's conclusion, not the line copied from the key."""

from __future__ import annotations

import json

from retrieval.solution_check import MATCH, MISMATCH, UNKNOWN, check_solution

LETTERS = lambda s: json.dumps({"kind": "letters", "letters": s})  # noqa: E731
EXACT = lambda v: json.dumps({"kind": "exact", "value": str({"is_range": False, "exact_value": v, "min_value": None, "max_value": None})})  # noqa: E731
RANGE = lambda lo, hi: json.dumps({"kind": "exact", "value": str({"is_range": True, "exact_value": None, "min_value": lo, "max_value": hi})})  # noqa: E731


def test_multi_letter_conclusion_matches():
    sol = "Reasoning...\n\nHence, the correct statements are **A, C, and D**.\n\nCorrect answer: (A, C, D)"
    assert check_solution(LETTERS("A, C, D"), sol)[0] == MATCH


def test_two_separate_bold_letters_match():
    sol = "...\n### Conclusion: The pair(s) are **(A)** and **(B)**.\n\nCorrect answer: (A, B)"
    assert check_solution(LETTERS("A, B"), sol)[0] == MATCH


def test_body_that_concludes_differently_is_a_mismatch_even_when_the_copied_line_agrees():
    sol = "So the correct option is **(B)**.\n\nCorrect answer: (A)"
    outcome, detail = check_solution(LETTERS("A"), sol)
    assert outcome == MISMATCH and "B" in detail


def test_numeric_ignores_list_numbers_and_subscripts():
    sol = "**4. Tension:** use v = sqrt(T/mu)\n$$T = 5\\text{ N}$$\n\n**Final Answer:** The value of $r_1$ is **$5$**.\n\nNumerical answer: {}"
    assert check_solution(EXACT(5), sol)[0] == MATCH


def test_decimal_answer_is_not_cut_at_the_leading_digit():
    sol = "so the ratio is\n\n**Final Answer:** **1.45**\n\nNumerical answer: {}"
    assert check_solution(RANGE(1.40, 1.50), sol)[0] == MATCH


def test_hedged_body_is_unknown_not_wrong():
    sol = "The time is **$2\\text{ s}$** (or **$8\\text{ s}$** by relative velocity).\n\nNumerical answer: {}"
    assert check_solution(EXACT(2), sol)[0] == UNKNOWN


def test_already_checked_and_missing_inputs():
    assert check_solution(LETTERS("A"), "Official answer: A\n\nWorked solution (written by Aeropt; its final answer was checked against the official key): ...")[0] == MATCH
    assert check_solution(LETTERS("A"), "")[0] == UNKNOWN
    assert check_solution("not json", "text")[0] == UNKNOWN
