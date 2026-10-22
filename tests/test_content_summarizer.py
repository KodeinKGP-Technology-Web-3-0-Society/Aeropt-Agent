"""Phase 6 — content_summarizer unit tests (mocked LLM).

Run:  python -m unittest tests.test_content_summarizer -v
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agents.content_summarizer import (
    MAX_CARDS,
    _extract_json_array,
    _normalize_card,
    summarize_to_slide_cards,
)

MOCK_FOUR_CARDS = [
    {
        "title": "Statement",
        "body": "Net force on a body equals mass times acceleration.",
        "card_type": "concept",
        "formula": "$\\vec{F}_{net} = m\\vec{a}$",
    },
    {
        "title": "Component form",
        "body": "Resolve forces along axes: $\\sum F_x = ma_x$.",
        "card_type": "formula",
        "formula": "$\\sum F_x = ma_x$",
    },
    {
        "title": "Atwood machine",
        "body": "Two masses over a pulley link accelerations via constraint.",
        "card_type": "example",
    },
    {
        "title": "Exam trap",
        "body": "Pick a sign convention before writing force equations.",
        "card_type": "takeaway",
    },
]

LONG_CORE_TEXT = (
    "Newton's Second Law: the rate of change of momentum is proportional to net force. "
    * 40
).strip()


def _mock_groq_four_cards(*_args, **_kwargs):
    return json.dumps(MOCK_FOUR_CARDS), "mock-model"


class TestContentSummarizer(unittest.TestCase):
    @patch("agents.content_summarizer.complete_text", side_effect=_mock_groq_four_cards)
    def test_summarize_returns_at_most_four_cards(self, _mock_llm) -> None:
        cards = summarize_to_slide_cards(
            core_text=LONG_CORE_TEXT,
            latex_formulae_core=r"\vec{F}=m\vec{a}",
            concept_label="Newton's Second Law",
            target_exam="JEE",
        )
        self.assertLessEqual(len(cards), MAX_CARDS)
        self.assertEqual(len(cards), 4)
        self.assertEqual(cards[0]["card_type"], "concept")
        self.assertIn("formula", cards[0])
        _mock_llm.assert_called_once()

    @patch(
        "agents.content_summarizer.complete_text",
        side_effect=lambda *_a, **_k: (
            json.dumps(MOCK_FOUR_CARDS + [{"title": "Extra", "body": "Should trim.", "card_type": "concept"}]),
            "mock-model",
        ),
    )
    def test_summarize_trims_excess_cards(self, _mock_llm) -> None:
        cards = summarize_to_slide_cards(
            core_text=LONG_CORE_TEXT,
            latex_formulae_core="",
            concept_label="Newton's Second Law",
        )
        self.assertEqual(len(cards), 4)

    def test_extract_json_array_strips_markdown_fence(self) -> None:
        raw = "```json\n" + json.dumps(MOCK_FOUR_CARDS[:2]) + "\n```"
        parsed = _extract_json_array(raw)
        self.assertEqual(len(parsed), 2)

    def test_normalize_card_rejects_invalid_type(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_card({"title": "Bad", "body": "text", "card_type": "invalid"}, 0)


if __name__ == "__main__":
    unittest.main()
