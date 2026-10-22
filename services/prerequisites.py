"""Shared prerequisite-gap selection for assessment and graded follow-up."""

import config
from typing import Any, Dict, List, Optional

def find_weak_direct_prereq(prereq_states: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Find the weakest direct prerequisite concept from a list of concept states.

    Priority:
      1. hop_count ASC   (direct prereqs first)
      2. proficiency_score ASC  (lower score = more in need)

    Returns the dictionary representing the weak prerequisite, or None if none are weak.
    """
    if not prereq_states:
        return None

    weak = [
        p for p in prereq_states
        if p.get("flagged_weak") or p.get("proficiency_score", 0.5) < config.WEAK_THRESHOLD
    ]
    if not weak:
        return None

    # Sort: hop_count ASC, proficiency_score ASC
    weak.sort(key=lambda p: (p.get("hop_count", 1), p.get("proficiency_score", 0.5)))
    return weak[0]
