"""Pedagogy rubric and LLM judge for evaluating JEE tutoring quality."""

PEDAGOGY_RUBRIC_PROMPT = """\
You are an expert pedagogy judge evaluating an AI tutor for competitive exam preparation (IIT-JEE).

Assess the tutor's response to the student on a 1–5 scale based on the following criteria:

CRITERIA:
1. Scaffolding & Pedagogy:
   - Does the tutor meet the student where they are?
   - On a first attempt or question, does it guide/nudge with Socratic inquiry instead of dumping a full worked solution?
   - When the student is stuck repeatedly or explicitly asks for the answer twice, does it reveal the solution clearly?
   - Does it address the student's specific sticking point or misconception?

2. Honesty & Grounding:
   - Are physics/chemistry principles accurate and mathematically sound?
   - Does it avoid fabricating check notes ("Checked with calculate") when no calculation was performed?
   - Does it refuse out-of-scope subjects (e.g. pure mathematics) politely?

3. Brevity & Clarity:
   - Is the response focused, clear, and appropriately concise (not overwhelming the student with text walls)?
   - Are formulas formatted cleanly in standard LaTeX?

SCORING GUIDE:
- Score 1 (Harmful / Incompetent): Completely gives away full solution immediately on first attempt, provides physically incorrect assertions, or invents hallucinated facts.
- Score 2 (Poor): Rigid, verbose lecturing, ignores the student's direct question, or offers confusing and unhelpful steps.
- Score 3 (Acceptable): Factually correct physics, but overly verbose, lectures rather than guides, or misses a chance for interactive engagement.
- Score 4 (Good): Clear explanation, helpful guiding questions/nudges, correct physics, appropriately concise.
- Score 5 (Master Tutor): Pinpoints the misconception immediately, provides a crisp Socratic nudge, leaves space for student active thinking, perfectly calibrated tone and brevity.

INPUT:
Student message:
{student_message}

Tutor response:
{tutor_response}

Context (if any):
{context}

Respond strictly in JSON format:
{{
  "score": <integer from 1 to 5>,
  "reasoning": "<two or three sentences explaining the score based on the rubric>",
  "scaffolding_appropriate": <true|false>,
  "factually_sound": <true|false>
}}
"""
