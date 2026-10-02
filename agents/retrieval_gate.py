"""
Retrieval Gate
Evaluates whether a turn actually needs fresh GraphRAG grounding, 
or if it can safely skip retrieval (saving time, Neo4j, and Gemini embed calls).
"""
import logging
from typing import Dict, Any

from openai import OpenAI
import config
from utils.llm import groq_complete

logger = logging.getLogger(__name__)

_groq = OpenAI(api_key=config.GEMINI_API_KEYS[0], base_url="https://generativelanguage.googleapis.com/v1beta/openai/")

_META_PROMPT = """\
You are a classifying agent for a tutoring system.
Determine if the student's message is a "meta" or "repair" turn about the immediately preceding conversation. 
A meta/repair turn means the student is asking to clarify the previous answer, asking why they were marked wrong, asking to rephrase/shorten the last response, or otherwise talking ABOUT the conversation rather than asking a brand new physics question.

Conversation Context:
{{context}}

Student Message: "{{query}}"

If it is a meta/repair turn, output ONLY the word TRUE.
If it is a new question or topic, output ONLY the word FALSE.
"""

def is_meta_repair_turn(query: str, recent_turns: list) -> bool:
    if not query:
        return False
        
    context_lines = []
    for t in recent_turns[-2:]: # Last 2 turns
        role = t.get("role", "")
        content = t.get("content", "")[:200]
        context_lines.append(f"  [{role}]: {content}")
    context = "\n".join(context_lines) if context_lines else "  (none)"
    
    prompt = _META_PROMPT.replace("{{context}}", context).replace("{{query}}", query)
    
    try:
        model = getattr(config, "SMALL_MODEL_FALLBACK", ["llama3-8b-8192"])[0]
        if hasattr(config, "ORCHESTRATOR_MODEL") and not getattr(config, "SMALL_MODEL_FALLBACK", None):
            model = config.ORCHESTRATOR_MODEL
            
        raw, _ = groq_complete(
            client=_groq,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=[],
            temperature=0.0,
            max_tokens=10,
            stream=False
        )
        return "true" in raw.lower()
    except Exception as e:
        logger.warning(f"Meta turn classification failed: {e}")
        return False

def should_skip_retrieval(state: Dict[str, Any]) -> bool:
    # The local backend gates deterministically (retrieval/gate.py) — no model call,
    # four outcomes instead of two, and the concept check happens after resolution.
    if config.local_retrieval():
        from retrieval.gate import decide

        if state.get("_force_retrieval"):
            return False
        decision = decide(
            str(state.get("query") or ""),
            recent_turns=list(state.get("recent_turns") or []),
            state=state,
        )
        if decision.action in {"skip", "clarify"}:
            logger.info("[RetrievalGate] Skipping retrieval: %s", decision.reason)
            return True
        return False

    # 1. Unresolved primary concept — nothing to retrieve against. This one
    #    holds even under _force_retrieval: it is a data fact, not a heuristic.
    primary_id = state.get("primary_concept_id")
    if not primary_id:
        logger.info("[RetrievalGate] Skipping retrieval: no primary_concept_id resolved.")
        return True

    # 2. Explicit agent decision.
    #    The gates below guess whether this turn needs retrieval. When the
    #    autonomous agent calls retrieve_content it has already made that
    #    judgement with far more context than a keyword-free classifier prompt,
    #    so a guess must not silently cancel it — that would make the tool lie
    #    about what it did.
    if state.get("_force_retrieval"):
        return False

    # 3. Prefetch cache hit
    if state.get("_prefetch_fast_forward"):
        logger.info("[RetrievalGate] Skipping retrieval: fast-forwarding from prefetch cache.")
        return True

    # 4. Meta/repair turn
    intent = state.get("intent", "")
    if intent in {"doubt", "follow_up_response"}:
        query = state.get("query", "")
        recent_turns = state.get("recent_turns", [])
        if is_meta_repair_turn(query, recent_turns):
            logger.info("[RetrievalGate] Skipping retrieval: meta/repair turn detected.")
            return True
            
    return False
