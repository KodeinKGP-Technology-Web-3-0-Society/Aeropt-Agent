"""
Regression tests for agents.concept_resolver_node — specifically the F1 fix:
off-syllabus / unmatched queries must leave primary_concept_id empty instead of
falling back to an arbitrary concept.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.concept_resolver import concept_resolver_node

_FAKE_CONCEPTS = [
    {"concept_id": "newtons_2nd_law", "concept_name": "Newton's Second Law", "chapter": "Laws of Motion"},
    {"concept_id": "projectile_motion", "concept_name": "Projectile Motion", "chapter": "Kinematics"},
]


class TestConceptResolverNode(unittest.TestCase):
    @patch("agents.concept_resolver.store")
    @patch("agents.concept_resolver.resolve_concept_id")
    def test_off_syllabus_query_stays_unresolved(self, mock_resolve, mock_store):
        """
        The old behaviour forced primary_concept_id = concepts[0] whenever the
        classifier's answer wasn't a valid id. A photosynthesis question should
        instead come back unresolved (empty primary_concept_id), not silently
        reassigned to "Newton's Second Law" just because it's first in the table.
        """
        mock_store.all_concepts.return_value = _FAKE_CONCEPTS
        mock_resolve.return_value = {
            "in_scope": False,
            "primary_concept_id": None,
            "secondary_concept_ids": [],
            "content_type_requested": "theory",
        }

        state = {
            "query": "Can you explain how photosynthesis converts sunlight into glucose?",
            "recent_turns": [],
            "roadmap_concept_id": None,
        }
        result = concept_resolver_node(state)

        self.assertEqual(result["primary_concept_id"], "")
        self.assertIsNone(result["error"])

    @patch("agents.concept_resolver.store")
    @patch("agents.concept_resolver.resolve_concept_id")
    def test_in_scope_query_passes_through(self, mock_resolve, mock_store):
        mock_store.all_concepts.return_value = _FAKE_CONCEPTS
        mock_resolve.return_value = {
            "in_scope": True,
            "primary_concept_id": "projectile_motion",
            "secondary_concept_ids": [],
            "content_type_requested": "theory",
        }

        state = {"query": "Explain projectile motion", "recent_turns": [], "roadmap_concept_id": None}
        result = concept_resolver_node(state)

        self.assertEqual(result["primary_concept_id"], "projectile_motion")
        self.assertIsNone(result["error"])

    @patch("agents.concept_resolver.store")
    def test_empty_taxonomy_is_a_real_error_not_unresolved(self, mock_store):
        """An empty concepts table is an ingestion problem, not an off-syllabus query."""
        mock_store.all_concepts.return_value = []

        result = concept_resolver_node({"query": "anything", "recent_turns": [], "roadmap_concept_id": None})

        self.assertIsNotNone(result.get("error"))

    @patch("db.supabase_client.db.read_concepts", side_effect=AssertionError("Redundant REST request"))
    @patch("agents.concept_resolver.store.all_concepts", return_value=_FAKE_CONCEPTS)
    @patch("agents.concept_resolver.resolve_concept_id")
    def test_resolution_uses_the_cached_retrieval_catalogue(self, mock_resolve, mock_catalogue, mock_rest):
        mock_resolve.return_value = {"in_scope": True, "primary_concept_id": "newtons_2nd_law"}
        result = concept_resolver_node({"query": "Explain Newton's second law"})
        self.assertEqual(result["primary_concept_id"], "newtons_2nd_law")
        mock_catalogue.assert_called_once_with()
        mock_rest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
