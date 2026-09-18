"""
MCP Tool: Search Knowledge Graph
Encapsulates the 5-Stage GraphRAG Pipeline as a standalone tool.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import config
from db.supabase_client import db
from retrieval.local_graph import graph_client  # backend-aware: Neo4j when hosted, the local index when not
from google import genai

_genai_client = genai.Client(api_key=config.GEMINI_API_KEY)


class SearchKnowledgeGraphArgs(BaseModel):
    query: str = Field(..., description="The student's query to embed for semantic search.")
    primary_concept_id: str = Field(..., description="The main concept ID to search within.")
    secondary_concept_ids: List[str] = Field(default_factory=list, description="Fallback concept IDs.")
    content_type: str = Field(default="theory", description="Type of content: theory, practice, pyq, example.")
    target_exam: str = Field(default="JEE_ADVANCED", description="Exam standard to filter by.")
    prereq_states: List[Dict[str, Any]] = Field(default_factory=list, description="List of prereq mastery states.")
    difficulty_target: float = Field(default=3.0, description="Target difficulty level (1-5).")
    difficulty_min: int = Field(default=1, description="Minimum allowed difficulty.")
    difficulty_max: int = Field(default=5, description="Maximum allowed difficulty.")
    seen_content_ids: List[str] = Field(default_factory=list, description="List of content IDs the student has already seen.")


def _embed_query(text: str) -> List[float]:
    """Embed with whichever model built the index in use.

    Mixing the two is not a degradation, it is nonsense: a 3072-d Gemini vector has
    no meaningful dot product with a 256-d static one, and the local client rejects
    it rather than ranking noise.
    """
    if config.local_retrieval():
        from retrieval import embedder

        return [float(x) for x in embedder.encode_one(text)]
    result = _genai_client.models.embed_content(
        model=config.EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


def search_knowledge_graph(args: SearchKnowledgeGraphArgs) -> List[Dict[str, Any]]:
    """
    Executes the 5-Stage GraphRAG pipeline to fetch the best educational chunks.
    """
    try:
        # ── STAGE 1: Graph Filter ─────────────────────────────────────────────
        MIN_PRIMARY_CANDIDATES = 3
        _EXAMPLE_CASCADE = ["example", "practice", "pyq"]

        def _get_for_type(ctype: Optional[str], cid: str, limit: int = 30) -> List[str]:
            return graph_client().get_content_ids_for_concept(
                cid,
                content_type=ctype,
                target_exam=args.target_exam,
                difficulty_max=args.difficulty_max,
                limit=limit,
                ignore_content_type=(ctype is None),
            )

        candidate_ids: List[str] = []

        if args.content_type == "example":
            for tier_type in _EXAMPLE_CASCADE:
                candidate_ids.extend(_get_for_type(tier_type, args.primary_concept_id))
                if len(candidate_ids) >= MIN_PRIMARY_CANDIDATES:
                    break
        else:
            candidate_ids = _get_for_type(args.content_type, args.primary_concept_id)
            if len(candidate_ids) < MIN_PRIMARY_CANDIDATES:
                candidate_ids = _get_for_type(None, args.primary_concept_id)

        if len(candidate_ids) < MIN_PRIMARY_CANDIDATES and args.secondary_concept_ids:
            for sec_id in args.secondary_concept_ids:
                candidate_ids.extend(_get_for_type(args.content_type, sec_id, limit=15))

        seen: set = set()
        candidate_ids = [x for x in candidate_ids if not (x in seen or seen.add(x))]

        # ── STAGE 2: Prereq Penalty ───────────────────────────────────────────
        has_broken_foundation = any(
            p.get("hop_count", 1) == 1 and p.get("proficiency_score", 0.5) < config.WEAK_THRESHOLD
            for p in args.prereq_states
        )
        if has_broken_foundation and len(candidate_ids) > 1:
            raw_candidates = db.read_content_by_ids(candidate_ids)
            filtered_ids = [
                c["content_id"] for c in raw_candidates 
                if c.get("difficulty", 1) < 3
            ]
            if filtered_ids:
                candidate_ids = filtered_ids

        # ── STAGE 3: Semantic Rank (Vector Search) ────────────────────────────
        query_vector = _embed_query(args.query)
        vector_results = graph_client().vector_search(
            query_vector=query_vector,
            top_k=20,
            candidate_ids=candidate_ids if candidate_ids else None,
        )
        
        if not vector_results:
            return []

        # ── STAGE 4: Composite Score ──────────────────────────────────────────
        top_20_ids = [r["content_id"] for r in vector_results]
        content_metadata = {c["content_id"]: c for c in db.read_content_by_ids(top_20_ids)}
        
        seen_content = set(args.seen_content_ids)
        
        scored_candidates = []
        for v in vector_results:
            cid = v["content_id"]
            meta = content_metadata.get(cid)
            if not meta:
                continue
                
            semantic_score = v.get("score", 0)
            diff = meta.get("difficulty", 3)
            
            diff_fit = max(0, 1.0 - (abs(diff - args.difficulty_target) * 0.25))
            novelty = 0.2 if cid in seen_content else 1.0
            quality = 1.0
            
            composite = (
                (0.50 * semantic_score) +
                (0.25 * diff_fit) +
                (0.15 * novelty) +
                (0.10 * quality)
            )
            scored_candidates.append((composite, meta))
            
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        top_k_ids = [meta["content_id"] for _, meta in scored_candidates[:config.NEO4J_VECTOR_TOP_K]]

        # ── STAGE 5: Supabase Fetch ───────────────────────────────────────────
        if not top_k_ids:
            return []
            
        final_chunks = db.read_content_by_ids(top_k_ids)
        
        id_to_chunk = {c["content_id"]: c for c in final_chunks}
        ordered_chunks = [id_to_chunk[cid] for cid in top_k_ids if cid in id_to_chunk]
        
        return ordered_chunks

    except Exception as exc:
        import logging
        logging.exception("search_knowledge_graph tool failed.")
        return []
