"""
Practice Analyzer Agent

Receives a batch of user answers, evaluates them, updates the DB proficiency,
and provides a diagnostic summary and recommendations to the user.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from openai import OpenAI

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import groq_complete

logger = logging.getLogger("jee_tutor.practice_analyzer")
_groq = OpenAI(api_key=config.GEMINI_API_KEYS[0], base_url="https://generativelanguage.googleapis.com/v1beta/openai/")

_PROMPT = """\
You are an expert JEE physics tutor. The student just submitted a practice batch.
Here are the results of their attempt:

{results_json}

Provide a concise, encouraging diagnostic summary of their performance.
Tell them what they did well, what their main weak points are, and what they should practice or revise next.
Keep it under 150 words. Use Markdown for emphasis.
"""

def practice_analyzer_node(state: TutorState) -> Dict[str, Any]:
    user_id = state["user_id"]
    session_id = state["session_id"]
    
    query = (state.get("query") or "").strip()
    
    results = []
    if query.startswith("SUBMISSION::"):
        try:
            results = json.loads(query[len("SUBMISSION::"):])
        except Exception:
            pass
            
    if not results:
        return {"response": "I received your submission, but it looks empty. Let's try again!"}

    concept_id = state.get("primary_concept_id") or "physics concepts"
    
    correct_count = 0
    total = len(results)
    
    for res in results:
        is_correct = res.get("is_correct", False)
        if is_correct:
            correct_count += 1
            
        db.insert_user_attempt(
            user_id=user_id,
            concept_id=concept_id,
            content_id=res.get("question_id", "generated"),
            is_correct=is_correct,
            error_type="calculation" if not is_correct else None,
            time_taken_sec=res.get("time_taken", 60),
            session_id=session_id,
            source="practice",
        )
        
        db.update_user_concept_state(
            user_id=user_id,
            concept_id=concept_id,
            is_correct=is_correct,
            error_type="calculation" if not is_correct else None,
            hop_count=0
        )

    results_str = json.dumps(results, indent=2)
    prompt = _PROMPT.format(results_json=results_str)
    
    try:
        raw, _ = groq_complete(
            client=_groq,
            model=config.SOLVER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SOLVER_FALLBACK_MODELS,
            temperature=0.3,
            max_tokens=800
        )
        
        feedback = raw.strip()
        score_summary = f"**Here is where you stand after this attempt:**\nYou got {correct_count} out of {total} correct."
        
        db.insert_chat_turn(
            session_id=session_id,
            role="assistant",
            content=f"{score_summary}\n\n{feedback}",
            turn_index=state.get("interaction_count", 1) * 2,
            agent_used="solver",
        )
        
        return {
            "response": feedback,
            "score_summary": score_summary,
            "agent_used": "solver",
            "error": None
        }
        
    except Exception as exc:
        logger.exception("Practice Analyzer failed")
        return {"error": f"Practice Analyzer failed: {exc}"}
