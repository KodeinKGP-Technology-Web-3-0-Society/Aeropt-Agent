from agents.solver import _parse_steps

def test_parse_steps_splits_on_headers():
    markdown = """**Intuition**:
This is the core idea.

**Core Mechanism**:
Here is the rigor.

**Mathematical Proof/Formula**:
$$ F = ma $$

**Takeaway**:
We learned something."""
    steps = _parse_steps(markdown)
    assert len(steps) == 4
    assert steps[0]["step_id"] == "s1"
    assert "Intuition" in steps[0]["text"]
    assert steps[1]["step_id"] == "s2"
    assert "Core Mechanism" in steps[1]["text"]
    assert steps[2]["step_id"] == "s3"
    assert "Mathematical Proof" in steps[2]["text"]
    assert steps[3]["step_id"] == "s4"
    assert "Takeaway" in steps[3]["text"]

def test_parse_steps_splits_on_numbered_lists():
    markdown = """Step 1: First do this.
Step 2: Then do this.
1. And this.
2. Finally this."""
    steps = _parse_steps(markdown)
    assert len(steps) == 4
    assert steps[0]["step_id"] == "s1"
    assert "First do this" in steps[0]["text"]
    assert steps[1]["step_id"] == "s2"
    assert "Then do this" in steps[1]["text"]
    assert steps[2]["step_id"] == "s3"
    assert "And this" in steps[2]["text"]
    assert steps[3]["step_id"] == "s4"
    assert "Finally this" in steps[3]["text"]
