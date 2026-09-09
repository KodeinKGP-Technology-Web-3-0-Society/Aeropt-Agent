from agents.prereq_gap import find_weak_direct_prereq

def test_find_weak_direct_prereq_empty():
    assert find_weak_direct_prereq([]) is None

def test_find_weak_direct_prereq_none_weak():
    prereqs = [
        {"concept_id": "A", "proficiency_score": 0.8, "hop_count": 1, "flagged_weak": False},
        {"concept_id": "B", "proficiency_score": 0.6, "hop_count": 2, "flagged_weak": False},
    ]
    assert find_weak_direct_prereq(prereqs) is None

def test_find_weak_direct_prereq_finds_weak():
    prereqs = [
        {"concept_id": "A", "proficiency_score": 0.8, "hop_count": 1},
        {"concept_id": "B", "proficiency_score": 0.3, "hop_count": 1},  # weak
    ]
    res = find_weak_direct_prereq(prereqs)
    assert res is not None
    assert res["concept_id"] == "B"

def test_find_weak_direct_prereq_prioritizes_hop_count():
    prereqs = [
        {"concept_id": "A", "proficiency_score": 0.1, "hop_count": 2},  # weaker but further
        {"concept_id": "B", "proficiency_score": 0.3, "hop_count": 1},  # direct prereq
    ]
    res = find_weak_direct_prereq(prereqs)
    assert res is not None
    assert res["concept_id"] == "B"
