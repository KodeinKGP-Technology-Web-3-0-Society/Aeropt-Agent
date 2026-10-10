"""
F5 regression: raw backend exception text must never reach the student-facing
ChatMessageResponse.error field (Aeropt-frontend throws it verbatim into the
chat UI as-is) — it should be logged server-side and replaced with a generic,
actionable message.
"""

from __future__ import annotations

import unittest

import api_server


class TestStudentFacingError(unittest.TestCase):
    def test_none_passes_through(self):
        self.assertIsNone(api_server._student_facing_error(None))

    def test_raw_exception_text_is_not_leaked(self):
        raw = "GraphRAG Retriever failed: 'NoneType' object has no attribute 'get'"
        message = api_server._student_facing_error(raw)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertNotIn("NoneType", message)
        self.assertNotIn("GraphRAG", message)

    def test_message_is_generic_and_actionable(self):
        message = api_server._student_facing_error("some internal detail")
        assert message is not None
        self.assertIn("try again", message.lower())


if __name__ == "__main__":
    unittest.main()
