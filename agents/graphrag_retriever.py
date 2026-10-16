"""GraphRAG retriever node — the graph's entry point into retrieval.

One path: `retrieval/bridge.py` resolves, gates, filters for novelty and runs hybrid
search over the Supabase retrieval Postgres, then returns this node's state updates.
(The Neo4j Aura + Gemini-embedding path that used to live here is gone: it embedded
queries in a 3072-d space the corpus does not use.)
"""

from __future__ import annotations

from typing import Any, Dict

from models.state import TutorState


def graphrag_retriever_node(state: TutorState) -> Dict[str, Any]:
    from retrieval.bridge import retrieve_for_state

    return retrieve_for_state(dict(state))
