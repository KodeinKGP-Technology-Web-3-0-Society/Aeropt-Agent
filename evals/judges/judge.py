"""LLM judge implementation for scoring tutoring interactions and checking calibration agreement."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.judges.rubric import PEDAGOGY_RUBRIC_PROMPT

logger = logging.getLogger("jee_tutor.evals.judge")

CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration.json"


@dataclass
class JudgeResult:
    score: int
    reasoning: str
    scaffolding_appropriate: bool
    factually_sound: bool
    raw_response: str = ""


def judge_turn(
    student_message: str,
    tutor_response: str,
    context: str = "",
    model: Optional[str] = None,
) -> JudgeResult:
    """Evaluate a single turn using the pedagogy rubric."""
    from utils.llm import groq_complete
    import config

    prompt = PEDAGOGY_RUBRIC_PROMPT.format(
        student_message=student_message,
        tutor_response=tutor_response,
        context=context or "General JEE study session",
    )

    judge_model = model or getattr(config, "JUDGE_MODEL", config.SOCRATIC_MODEL)
    raw, _ = groq_complete(
        client=None,
        model=judge_model,
        messages=[{"role": "user", "content": prompt}],
        fallback_models=config.SMALL_MODEL_FALLBACK,
        temperature=0.1,
        max_tokens=600,
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(raw)
        return JudgeResult(
            score=int(data.get("score", 3)),
            reasoning=str(data.get("reasoning", "")),
            scaffolding_appropriate=bool(data.get("scaffolding_appropriate", True)),
            factually_sound=bool(data.get("factually_sound", True)),
            raw_response=raw,
        )
    except Exception as exc:
        logger.warning("Failed to parse judge output: %s (raw: %r)", exc, raw)
        return JudgeResult(
            score=3,
            reasoning=f"Unparseable judge output: {exc}",
            scaffolding_appropriate=True,
            factually_sound=True,
            raw_response=raw,
        )


def evaluate_calibration(model: Optional[str] = None) -> Dict[str, Any]:
    """Score the 10 hand-labelled calibration examples and report agreement."""
    with open(CALIBRATION_PATH, "r", encoding="utf-8") as f:
        cases: List[Dict[str, Any]] = json.load(f)

    results = []
    exact_matches = 0
    close_matches = 0  # within 1 point

    for case in cases:
        res = judge_turn(
            case["student_message"],
            case["tutor_response"],
            context="",
            model=model,
        )
        expected = case["expected_score"]
        diff = abs(res.score - expected)
        if diff == 0:
            exact_matches += 1
            close_matches += 1
        elif diff <= 1:
            close_matches += 1

        results.append(
            {
                "id": case["id"],
                "expected": expected,
                "predicted": res.score,
                "diff": diff,
                "reasoning": res.reasoning,
            }
        )

    total = len(cases)
    return {
        "total_cases": total,
        "exact_agreement_rate": round(exact_matches / total, 3) if total else 0.0,
        "within_one_agreement_rate": round(close_matches / total, 3) if total else 0.0,
        "details": results,
    }
