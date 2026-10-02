import json
import logging
from typing import Dict, Any, List

from openai import OpenAI
import config
from utils.llm import groq_complete

logger = logging.getLogger("jee_tutor.study_planner")

_PLANNER_PROMPT = """You are an elite academic counselor creating a personalized study plan for a student preparing for the {exam}.
The student's preferred depth is: {depth}.

Their current mastery across tracked concepts is:
{trackers}

Based on this data, construct a logical study queue for today.
Choose 3 to 5 concepts to cover.
Prioritize:
1. "REVISE" for any concept where mastery is low (<50) or flagged as weak.
2. "LEARN" for new concepts that they haven't seen yet or have 0 mastery.
3. "PRACTICE" for concepts they have good mastery in (>70) but need to solidify.

Output a valid JSON array of objects, where each object has:
- `concept_id`: string
- `title`: string (concept name)
- `action`: string (one of "LEARN", "REVISE", "PRACTICE")
- `estimated_minutes`: integer (suggested time in minutes, e.g., 15, 25, 45)
- `reason`: string (a short 1-sentence explanation of why you chose this concept for today)

Respond ONLY with valid JSON. No markdown fences.
"""

def generate_study_plan(user_profile: Dict[str, Any], trackers: List[Dict[str, Any]], available_concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    client = OpenAI(api_key=config.GEMINI_API_KEYS[0], base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    
    # Format tracker data
    tracker_text = ""
    for t in trackers:
        concept = t.get("title", t.get("concept_id"))
        score = t.get("mastery", 0)
        weak = " (Weak!)" if t.get("flagged_weak") else ""
        tracker_text += f"- {concept}: {score}% mastery{weak}\n"
        
    if not tracker_text:
        tracker_text = "No history available. Start with fundamentals.\n"
        # Provide some available concepts if no history
        for c in available_concepts[:5]:
            tracker_text += f"Available new concept: {c.get('title')} ({c.get('concept_id')})\n"

    prompt = _PLANNER_PROMPT.format(
        exam=user_profile.get("target_exam", "JEE"),
        depth=user_profile.get("preferred_depth", "standard"),
        trackers=tracker_text
    )

    try:
        response_text, _ = groq_complete(
            client=client,
            model=config.ORCHESTRATOR_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.3,
            max_tokens=1000,
        )
        
        # Clean potential markdown fences
        text = response_text.strip()
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()
        
        plan = json.loads(text)
        if not isinstance(plan, list):
            logger.error(f"Planner output is not a list: {plan}")
            return []
            
        # Ensure correct fields
        valid_plan = []
        for item in plan:
            if "concept_id" in item and "action" in item:
                # Add extra fields expected by frontend
                item["status"] = "pending"
                item["when"] = "today"
                valid_plan.append(item)
                
        return valid_plan

    except Exception as e:
        logger.exception("Failed to generate study plan")
        return []
