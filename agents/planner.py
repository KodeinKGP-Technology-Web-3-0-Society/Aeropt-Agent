from typing import Dict, Any, List, Optional
import config

def compute_personalization_strategy(
    target_state: Dict[str, Any],
    prereq_states: List[Dict[str, Any]],
    learning_profile: Optional[Dict[str, Any]],
    content_type: str,
    query: str,
    socratic_turn_count: int = 0
) -> Dict[str, Any]:
    """
    Canonical personalization heuristic. Computes the teaching mode, framing, and difficulty
    target for a specific user and concept based on their profile, mastery state, and prerequisite health.
    """
    target_score = target_state.get("proficiency_score", 0.5) if target_state else 0.5
    is_cold_start = target_state.get("is_cold_start", True) if target_state else True
    
    velocity = (learning_profile.get("learning_velocity") if learning_profile else "average") or "average"
    pref_type = (learning_profile.get("preferred_content_type") if learning_profile else "theory") or "theory"

    if prereq_states:
        prereq_avg = sum(s.get("proficiency_score", 0.5) for s in prereq_states) / len(prereq_states)
        weak_prereqs = [s for s in prereq_states if s.get("proficiency_score", 0.5) < 0.5 and s.get("hop_count", 1) == 1]
        weakest_prereq = min(weak_prereqs, key=lambda x: x.get("proficiency_score", 0.5)) if weak_prereqs else None
    else:
        prereq_avg = 0.5
        weakest_prereq = None

    teaching_mode = "direct"
    framing = "normal"
    prereq_to_teach_first = None

    # Core logic combining cold start, prereq gap, socratic, and mastery
    if is_cold_start:
        teaching_mode = "direct"
        framing = "cold_start"
    elif weakest_prereq and target_score < 0.5:
        teaching_mode = "prereq_first"
        prereq_to_teach_first = weakest_prereq["concept_id"]
        framing = "revisit_gap"
    elif prereq_avg > 0.6 and target_score < 0.7:
        if socratic_turn_count >= getattr(config, "SOCRATIC_MAX_TURNS", 3):
            teaching_mode = "direct"
            framing = "normal"
        else:
            teaching_mode = "socratic"
            framing = "deepen"
    elif target_score > 0.7 or (pref_type == "practice" and target_score > 0.5):
        if velocity == "high" and target_score > 0.6:
            teaching_mode = "practice"
            framing = "mastery_check"
        elif target_score > 0.7:
            teaching_mode = "practice"
            framing = "mastery_check"

    # Explictly override if query asks for theory/practice
    lowered = (query or "").lower()
    explicit_theory = any(p in lowered for p in ("explain", "what is", "define"))
    if explicit_theory:
        teaching_mode = "direct"
    if content_type in ("practice", "pyq"):
        teaching_mode = "direct"

    # Difficulty targeting
    if velocity == "high":
        difficulty = target_score * 5.0 + 1.0
    elif velocity == "low":
        difficulty = target_score * 5.0 - 0.5
    else:
        difficulty = target_score * 5.0
        
    difficulty = max(1.0, min(5.0, difficulty))

    return {
        "teaching_mode": teaching_mode,
        "framing": framing,
        "difficulty_target": round(difficulty, 1),
        "prereq_to_teach_first": prereq_to_teach_first,
        "use_socratic": teaching_mode == "socratic"
    }

def planner_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Thin wrapper around compute_personalization_strategy for backwards compatibility
    with api_server prefetching.
    """
    from db.supabase_client import db
    user_id = state.get("user_id")
    primary_id = state.get("primary_concept_id")
    
    target_state = db.read_user_concept_state(user_id, primary_id)
    profile = db.read_user_learning_profile(user_id)
    
    # We omit prereq fetch here just to satisfy prefetch without neo4j spam
    return compute_personalization_strategy(
        target_state=target_state or {},
        prereq_states=[],
        learning_profile=profile,
        content_type=state.get("content_type_requested", "theory"),
        query=state.get("query", "")
    )
