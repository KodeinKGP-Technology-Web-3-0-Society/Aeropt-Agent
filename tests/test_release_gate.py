"""Tests for the release gate (F2).

Verifies that any student-facing text (whether original draft or checker rewrite)
passes through provenance filters, LaTeX hardening, and never leaks invented
images or unverified exam claims.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agents.runtime.release import release
from agents.runtime.toolbelt import ToolContext


class ReleaseGateTests(unittest.TestCase):
    def test_stubbed_checker_rewrite_filters_images_and_exam_claims(self):
        """F2 Acceptance Test:
        A stubbed checker rewrite containing ![x](https://example.com/a.png)
        and 'JEE Advanced 2016' comes out filtered.
        """
        raw_rewrite = (
            "Here is a problem: ![diagram](https://example.com/a.png)\n\n"
            "This is from JEE Advanced 2016 Paper 1.\n\n"
            "Find the acceleration."
        )
        ctx = ToolContext(user_id="u", session_id="s", state={"retrieved_chunks": []})
        out = release(raw_rewrite, ctx)

        # 1. Foreign image must be completely stripped
        self.assertNotIn("example.com", out)
        self.assertNotIn("https://", out)

        # 2. Unverified exam claim must be softened with a note
        self.assertIn("> **Note:**", out)
        self.assertIn("JEE Advanced-level practice", out)
        self.assertNotIn("This is from JEE Advanced 2016 Paper 1.", out)

    def test_valid_drawn_diagram_is_preserved(self):
        with patch("agents.runtime.provenance._is_drawn_diagram", return_value=True):
            text = "See figure: ![fbd](diagram:12345678901234567890)"
            out = release(text)
            self.assertIn("diagram:12345678901234567890", out)

    def test_latex_hardening_applied(self):
        # Standalone equation line without $$ should be hardened into $$...$$
        raw = r"I=\frac{1}{2}MR^2"
        out = release(raw)
        self.assertIn("$$", out)
        self.assertIn(r"\frac{1}{2}MR^2", out)

    def test_inline_tikz_converted(self):
        from services import diagrams

        picture = "\\begin{tikzpicture}\n  \\draw (0,0) -- (1,1);\n\\end{tikzpicture}"
        with patch.object(
            diagrams, "render_tikz", return_value={"id": "abcdef0123456789abcd", "path": "", "cached": True}
        ):
            with patch("agents.runtime.provenance._is_drawn_diagram", return_value=True):
                out = release(f"Before\n{picture}\nAfter")
                self.assertIn("diagram:abcdef0123456789abcd", out)
                self.assertNotIn("\\begin{tikzpicture}", out)


if __name__ == "__main__":
    unittest.main()
