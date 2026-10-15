"""Cloud Supabase retrieval engine: pgvector + PostgreSQL syllabus graph.

Drop-in replacement for the local SQLite index and Neo4j Aura backend.
Connects directly to the hosted Supabase database to perform:
1. Concept resolution via match_concepts (cosine similarity on public.concepts.embedding).
2. Prerequisite expansion via public.concept_edges.
3. Dense vector search via match_content (cosine similarity on public.content.embedding).
4. Content-type cascade (Theory -> Example -> Practice -> PYQ).
5. Novelty checking via public.served_items.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

from db.supabase_client import db
from retrieval import embedder

logger = logging.getLogger("aeropt.retrieval.supabase")

_CONFIDENCE_THRESHOLD = 0.42
_STRONG_THRESHOLD = 0.50


def warm() -> Dict[str, Any]:
    """Startup warm-up: verify connection and probe RPC functions."""
    t0 = time.time()
    try:
        probe_vec = embedder.encode_one("probe").tolist()
        res = db.retrieval_client.rpc(
            "match_concepts",
            {"query_embedding": probe_vec, "match_threshold": 0.0, "match_count": 1},
        ).execute()
        return {
            "warmed": True,
            "backend": "supabase",
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "sample_concept": res.data[0]["concept_id"] if res.data else None,
        }
    except Exception as exc:
        logger.warning("Supabase retrieval warm probe failed: %s", exc)
        return {"warmed": False, "error": str(exc)}


def resolve_concept(args: Any) -> Dict[str, Any]:
    """Map natural language query to concept IDs using Supabase pgvector search."""
    query = getattr(args, "query", "") or ""
    roadmap_concept_id = getattr(args, "roadmap_concept_id", None)
    study_stage = getattr(args, "study_stage", None)

    # 1. Fast Path if roadmap_concept_id provided
    if roadmap_concept_id:
        try:
            res = (
                db.retrieval_client.table("concepts")
                .select("concept_id, concept_name, chapter")
                .eq("concept_id", roadmap_concept_id)
                .limit(1)
                .execute()
            )
            if res.data:
                c = res.data[0]
                stage = study_stage if study_stage in {"theory", "example", "practice", "pyq"} else "theory"
                return {
                    "in_scope": True,
                    "primary_concept_id": c["concept_id"],
                    "secondary_concept_ids": [],
                    "content_type_requested": stage,
                    "concept_name": c["concept_name"],
                    "chapter": c["chapter"],
                    "in_jee_syllabus": True,
                    "grounding_strength": "strong",
                    "matched_by": "roadmap_concept_id",
                }
        except Exception as exc:
            logger.warning("Failed roadmap concept lookup: %s", exc)

    # 2. Vector match via Supabase
    q_vec = embedder.encode_one(query).tolist()
    try:
        res = db.retrieval_client.rpc(
            "match_concepts",
            {
                "query_embedding": q_vec,
                "match_threshold": 0.0,
                "match_count": 3,
            },
        ).execute()
        matches = res.data or []
    except Exception as exc:
        logger.error("Supabase match_concepts error: %s", exc)
        matches = []

    if not matches:
        return {
            "in_scope": False,
            "primary_concept_id": "",
            "secondary_concept_ids": [],
            "content_type_requested": "theory",
            "concept_name": "",
            "chapter": "",
            "in_jee_syllabus": False,
            "grounding_strength": "none",
            "matched_by": "none",
        }

    top = matches[0]
    sim = float(top.get("similarity", 0.0))
    secondary_ids = [m["concept_id"] for m in matches[1:] if m.get("concept_id")]

    # Detect requested content type from query text
    lowered = query.lower()
    content_type = "theory"
    if any(k in lowered for k in ("pyq", "previous year", "past year")):
        content_type = "pyq"
    elif any(k in lowered for k in ("practice", "question", "problem", "quiz", "test")):
        content_type = "practice"
    elif any(k in lowered for k in ("example", "solved example", "worked example", "solution")):
        content_type = "example"

    return {
        "in_scope": sim >= _CONFIDENCE_THRESHOLD,
        "primary_concept_id": top["concept_id"],
        "secondary_concept_ids": secondary_ids,
        "content_type_requested": content_type,
        "concept_name": top.get("concept_name", top["concept_id"]),
        "chapter": top.get("chapter", ""),
        "in_jee_syllabus": True,
        "grounding_strength": "strong" if sim >= _STRONG_THRESHOLD else ("moderate" if sim >= _CONFIDENCE_THRESHOLD else "weak"),
        "matched_by": "supabase_pgvector",
    }


def _get_prerequisites(concept_id: str) -> List[Dict[str, Any]]:
    """Fetch prerequisite concept edges from public.concept_edges."""
    if not concept_id:
        return []
    try:
        res = (
            db.retrieval_client.table("concept_edges")
            .select("requires")
            .eq("concept_id", concept_id)
            .execute()
        )
        edges = res.data or []
        req_ids = [e["requires"] for e in edges if e.get("requires")]
        if not req_ids:
            return []

        c_res = (
            db.retrieval_client.table("concepts")
            .select("concept_id, concept_name")
            .in_("concept_id", req_ids)
            .execute()
        )
        return c_res.data or []
    except Exception as exc:
        logger.warning("Failed to fetch prerequisites for %s: %s", concept_id, exc)
        return []


def _get_seen_content_ids(user_id: str) -> Set[str]:
    """Query public.served_items for questions already given to this student."""
    if not user_id:
        return set()
    try:
        res = (
            db.client.table("served_items")
            .select("content_id")
            .eq("user_id", user_id)
            .execute()
        )
        return {r["content_id"] for r in (res.data or []) if r.get("content_id")}
    except Exception:
        return set()


def retrieve_for_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute complete retrieval pipeline against Supabase and return GraphRAG-shaped state."""
    t0 = time.time()
    user_id = str(state.get("user_id") or "")
    session_id = str(state.get("session_id") or "")
    query = str(state.get("query") or "")
    primary_id = str(state.get("primary_concept_id") or "")
    content_type = str(state.get("content_type_requested") or "theory")
    target_exam = str(state.get("target_exam") or "JEE_ADVANCED")
    top_k = int(state.get("retrieval_top_k") or 5)

    if not query.strip():
        return {"retrieval_decision": "skip"}

    # 1. Resolve concept if not already resolved
    q_vec = embedder.encode_one(query).tolist()
    concept_meta = None

    if not primary_id:
        try:
            c_res = db.retrieval_client.rpc(
                "match_concepts",
                {"query_embedding": q_vec, "match_threshold": 0.0, "match_count": 3},
            ).execute()
            c_matches = c_res.data or []
            if c_matches:
                concept_meta = c_matches[0]
                primary_id = concept_meta["concept_id"]
        except Exception as exc:
            logger.warning("Concept match error: %s", exc)

    if not primary_id:
        return {
            "primary_concept_id": "",
            "retrieved_chunks": [],
            "retrieval_confidence": "unresolved",
            "retrieval_notes": ["No matching concept found in syllabus."],
            "retrieval_decision": "unresolved",
            "error": None,
        }

    # 2. Get prerequisites
    prereq_rows = _get_prerequisites(primary_id)
    prereqs = [
        {
            "concept_id": p["concept_id"],
            "concept_name": p.get("concept_name", p["concept_id"]),
            "hop_count": 1,
        }
        for p in prereq_rows
    ]

    # 3. Novelty exclusion set
    seen_ids = _get_seen_content_ids(user_id)

    # 4. Content Matching via Supabase
    # Cascade: Try specific concept + content_type -> then concept alone -> then broad chapter/query
    chunks_raw: List[Dict[str, Any]] = []

    # Map content_type to database format ('Theory', 'Solved Example', 'Practice Question', 'PYQ')
    db_type_map = {
        "theory": "Theory",
        "example": "Solved Example",
        "practice": "Practice Question",
        "pyq": "PYQ",
    }
    target_db_type = db_type_map.get(content_type.lower())

    try:
        # Pass 1: exact concept + content_type
        rpc_params = {
            "query_embedding": q_vec,
            "match_threshold": 0.35,
            "match_count": top_k * 2,
            "filter_concept_id": primary_id,
            "filter_chapter": None,
            "filter_content_type": target_db_type,
            "filter_target_exam": None,
        }
        res = db.retrieval_client.rpc("match_content", rpc_params).execute()
        chunks_raw = res.data or []

        # Pass 2: relax content type if not enough hits
        if len(chunks_raw) < 2 and target_db_type:
            rpc_params["filter_content_type"] = None
            res2 = db.retrieval_client.rpc("match_content", rpc_params).execute()
            existing_ids = {c["content_id"] for c in chunks_raw}
            for c in (res2.data or []):
                if c["content_id"] not in existing_ids:
                    chunks_raw.append(c)

        # Pass 3: broad semantic search if concept yielded no candidates
        if not chunks_raw:
            rpc_params["filter_concept_id"] = None
            rpc_params["match_count"] = top_k
            res3 = db.retrieval_client.rpc("match_content", rpc_params).execute()
            chunks_raw = res3.data or []

    except Exception as exc:
        logger.error("Supabase match_content query failed: %s", exc)
        chunks_raw = []

    # Format chunks into standard agent schema
    formatted_chunks: List[Dict[str, Any]] = []
    citations: List[str] = []

    for r in chunks_raw:
        cid = r["content_id"]
        is_seen = cid in seen_ids
        ctype = r.get("content_type", "Theory")
        chap = r.get("chapter", "Physics")

        formatted = {
            "content_id": cid,
            "content_type": ctype,
            "subject": "Physics",
            "chapter": chap,
            "unit": "",
            "core_text": r.get("core_text", ""),
            "solution": r.get("solution"),
            "difficulty": r.get("difficulty") or 3,
            "question_type": r.get("question_type") or "conceptual",
            "target_exam": r.get("target_exam") or target_exam,
            "primary_concept": r.get("primary_concept_id", primary_id),
            "primary_concept_id": r.get("primary_concept_id", primary_id),
            "secondary_concepts": "",
            "source_book": "OpenStax University Physics",
            "source_ref": "",
            "license": "CC BY-NC-SA 4.0",
            "heading": "",
            "fingerprint": cid,
            "in_syllabus": 1,
            "has_solution": 1 if r.get("solution") else 0,
            "structured": None,
            "citation": f"{chap} — {ctype}",
            "retrieval_score": round(float(r.get("similarity", 0.0)), 4),
            "seen_before": is_seen,
        }

        # Filter out questions student has already seen if we have alternatives
        if is_seen and len(chunks_raw) > top_k:
            continue

        formatted_chunks.append(formatted)
        citations.append(formatted["citation"])
        if len(formatted_chunks) >= top_k:
            break

    confidence = "ok" if formatted_chunks else "low"

    # 5. Learner state integration
    target_state = {}
    learning_profile = None
    socratic_turns = 0

    if user_id:
        try:
            target_state = db.read_user_concept_state(user_id, primary_id) or {}
            learning_profile = db.read_user_learning_profile(user_id)
        except Exception:
            pass

    if session_id and primary_id:
        try:
            socratic_turns = db.count_socratic_turns_for_concept(session_id, primary_id) or 0
        except Exception:
            pass

    from agents.planner import compute_personalization_strategy

    prereq_states = [
        {
            "concept_id": p["concept_id"],
            "concept_name": p.get("concept_name", p["concept_id"]),
            "proficiency_score": 0.5,
            "flagged_weak": False,
            "is_cold_start": True,
            "hop_count": 1,
        }
        for p in prereqs
    ]

    strategy = compute_personalization_strategy(
        target_state=target_state or {},
        prereq_states=prereq_states,
        learning_profile=learning_profile,
        content_type=content_type,
        query=query,
        socratic_turn_count=socratic_turns,
    )

    took_ms = round((time.time() - t0) * 1000, 1)

    return {
        "primary_concept_id": primary_id,
        "secondary_concept_ids": [p["concept_id"] for p in prereqs[:2]],
        "content_type_requested": content_type,
        "target_concept_state": target_state,
        "prereq_concept_states": prereq_states,
        "secondary_concept_states": [],
        "retrieved_chunks": formatted_chunks,
        "retrieval_confidence": confidence,
        "retrieval_citations": citations,
        "retrieval_notes": [] if confidence == "ok" else ["Retrieval confidence is low."],
        "retrieval_novelty": {"excluded_seen": len(seen_ids & {r['content_id'] for r in chunks_raw})},
        "retrieval_decision": "retrieve",
        "retrieval_took_ms": took_ms,
        "use_socratic": bool(strategy.get("use_socratic", False)),
        "socratic_turn_count": socratic_turns,
        "teaching_mode": strategy.get("teaching_mode"),
        "difficulty_target": strategy.get("difficulty_target"),
        "framing": strategy.get("framing"),
        "error": None,
    }
