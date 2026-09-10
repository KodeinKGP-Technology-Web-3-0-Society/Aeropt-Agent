"""
Regression tests for the LaTeX-hardening fix: raw retrieval chunks are
sanitized before an LLM ever sees them as "ground truth" source, and a
finished response gets one safe safety-net repair + issue-logging before it
reaches the student.
"""

from __future__ import annotations

import unittest

from utils.latex_guidelines import (
    LATEX_OUTPUT_RULES,
    balance_dollar_delimiters,
    find_latex_issues,
    sanitize_chunk_text_for_prompt,
)


class TestSanitizeChunkTextForPrompt(unittest.TestCase):
    def test_unicode_symbols_become_macros(self):
        out = sanitize_chunk_text_for_prompt("Angular speed ω = 2πf, torque τ.")
        self.assertIn(r"\omega", out)
        self.assertIn(r"\pi", out)
        self.assertIn(r"\tau", out)

    def test_stray_literal_n_becomes_newline_but_nu_survives(self):
        out = sanitize_chunk_text_for_prompt(r"F=m\nu\nE=mc^2")
        self.assertIn(r"\nu", out)
        self.assertNotIn(r"\nE", out)
        self.assertIn("\n", out)

    def test_double_escaped_macro_collapses(self):
        out = sanitize_chunk_text_for_prompt(r"\\vec{F} = m\\vec{a}")
        self.assertIn(r"\vec{F}", out)
        self.assertNotIn(r"\\vec", out)

    def test_empty_text_is_a_noop(self):
        self.assertEqual(sanitize_chunk_text_for_prompt(""), "")

    def test_ordinary_prose_untouched(self):
        text = "Newton's second law relates net force to mass and acceleration."
        self.assertEqual(sanitize_chunk_text_for_prompt(text), text)


class TestBalanceDollarDelimiters(unittest.TestCase):
    def test_unpaired_trailing_dollar_is_escaped(self):
        out = balance_dollar_delimiters("The force is $F = ma and that's it.")
        self.assertIn(r"\$", out)
        self.assertNotIn("is $F", out)

    def test_well_formed_inline_math_untouched(self):
        text = "The force is $F = ma$ along the axis."
        self.assertEqual(balance_dollar_delimiters(text), text)

    def test_well_formed_display_math_untouched(self):
        text = "$$\nF = ma\n$$"
        self.assertEqual(balance_dollar_delimiters(text), text)

    def test_no_dollars_is_a_noop(self):
        text = "Plain prose, no math here."
        self.assertEqual(balance_dollar_delimiters(text), text)


class TestFindLatexIssues(unittest.TestCase):
    def test_clean_text_has_no_issues(self):
        self.assertEqual(find_latex_issues("The force is $F = ma$."), [])

    def test_odd_dollar_count_is_flagged(self):
        issues = find_latex_issues("The force is $F = ma and more text.")
        self.assertTrue(any("odd number" in i for i in issues))

    def test_unbalanced_braces_in_math_span_flagged(self):
        issues = find_latex_issues(r"$$F = \frac{ma$$")
        self.assertTrue(any("unbalanced braces" in i for i in issues))

    def test_legacy_delimiters_flagged(self):
        issues = find_latex_issues(r"Use \(F = ma\) here.")
        self.assertTrue(any("\\(" in i or "delimiter" in i for i in issues))

    def test_empty_text_has_no_issues(self):
        self.assertEqual(find_latex_issues(""), [])


class TestLatexOutputRulesMentionsCorruptedSource(unittest.TestCase):
    def test_rule_about_not_copying_broken_source_exists(self):
        self.assertIn("reconstruct", LATEX_OUTPUT_RULES.lower())


if __name__ == "__main__":
    unittest.main()
