"""Map tutor pipeline state → structured flashcards / grading UI for the frontend."""

from __future__ import annotations

from agents.solver import prepare_student_markdown

_PICKER_TYPES = {"theory", "example"}


def _card_front(item: Dict[str, Any]) -> str:
    title = (item.get("title") or item.get("concept_name") or "").strip()
    core = (item.get("core_text") or item.get("preview") or "").strip()
    if title:
        return _truncate(title, 120)
    if core:
        first_line = core.split("\n", 1)[0].strip()
        return _truncate(first_line, 160)
    ctype = (item.get("content_type") or "Study next").replace("_", " ").title()
    return ctype


def _truncate(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_solution_step(solution: str) -> str:
    if not solution:
        return "Re-read the question and identify known quantities."
    for line in solution.splitlines():
        cleaned = line.strip().lstrip("0123456789.)-•* ").strip()
        if cleaned:
            return _truncate(cleaned, 200)
    return _truncate(solution, 200)


def _weakest_prereq(prereq_states: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not prereq_states:
        return None
    ranked = sorted(
        prereq_states,
        key=lambda p: (p.get("hop_count", 99), p.get("proficiency_score", 1.0)),
    )
    return ranked[0]


def _as_katex_block(text: str) -> str:
    """Wrap raw LaTeX-ish DB strings in $$ delimiters for KaTeX."""
    text = (text or "").strip()
    if not text or "$" in text:
        return text
    if "\\" in text or re.search(r"[=^_]", text):
        return f"$$\n{text}\n$$"
    return text


def build_mistake_brief(
    *,
    error_type: str,
    chunk: Optional[Dict[str, Any]],
    prereq_states: List[Dict[str, Any]],
    concept_id: str,
) -> str:
    concept_label = concept_id.replace("_", " ").title()
    core = (chunk or {}).get("core_text") or ""
    solution = (chunk or {}).get("solution") or ""
    formula_core = (chunk or {}).get("latex_formulae_core") or ""
    formula_solution = (chunk or {}).get("latex_formulae_solution") or ""
    weak = _weakest_prereq(prereq_states)
    weak_name = (weak or {}).get("concept_name") or (weak or {}).get("concept_id", "")

    if error_type == "conceptual":
        idea = _truncate(core.split("\n")[0] if core else f"Core idea for {concept_label}", 220)
        extra = f" Review **{weak_name}** first if that foundation feels shaky." if weak_name else ""
        return f"**Concept check:** {idea}{extra}"

    if error_type == "procedural":
        step = _first_solution_step(solution)
        return f"**Method check:** Verify your step order. Start here: {step}"

    if error_type == "calculation":
        formula = _truncate(formula_solution or formula_core or "Re-check units and algebra.", 180)
        return f"**Calculation check:** {_as_katex_block(formula)}"

    if error_type == "misread":
        return f"**Read carefully:** Restate what is given vs what you need to find.\n\n{_truncate(core, 240)}"

    return f"**Review {concept_label}:** Compare your approach with the worked solution and note where it diverged."


def build_remediation_cards(
    *,
    chunk: Optional[Dict[str, Any]],
    error_type: str,
    prereq_states: List[Dict[str, Any]],
    concept_id: str,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    formula = (chunk or {}).get("latex_formulae_core") or (chunk or {}).get("latex_formulae_solution") or ""
    solution = (chunk or {}).get("solution") or ""
    weak = _weakest_prereq(prereq_states)

    chunk_id = (chunk or {}).get("content_id") or concept_id

    if formula:
        cards.append(
            {
                "id": f"remediation-{chunk_id}-formula",
                "card_type": "formula",
                "front": "Key relation for this topic",
                "back": _as_katex_block(formula),
                "concept_id": concept_id,
                "content_id": (chunk or {}).get("content_id"),
                "selectable": False,
            }
        )

    if solution:
        cards.append(
            {
                "id": f"remediation-{chunk_id}-step",
                "card_type": "micro_step",
                "front": "Next step to verify",
                "back": prepare_student_markdown(_first_solution_step(solution)),
                "concept_id": concept_id,
                "content_id": (chunk or {}).get("content_id"),
                "selectable": False,
            }
        )

    if weak:
        weak_name = weak.get("concept_name") or weak.get("concept_id", "prerequisite")
        weak_id = weak.get("concept_id") or "prereq"
        cards.append(
            {
                "id": f"remediation-{chunk_id}-prereq-{weak_id}",
                "card_type": "prereq",
                "front": "Foundation to review",
                "back": weak_name,
                "concept_id": weak.get("concept_id"),
                "selectable": True,
            }
        )

    if not cards:
        cards.append(
            {
                "id": f"remediation-{chunk_id}-review",
                "card_type": "theory",
                "front": f"Review {concept_id.replace('_', ' ')}",
                "back": build_mistake_brief(
                    error_type=error_type or "conceptual",
                    chunk=chunk,
                    prereq_states=prereq_states,
                    concept_id=concept_id,
                ),
                "concept_id": concept_id,
                "selectable": False,
            }
        )

    return cards[:4]


def filter_picker_suggestions(suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in suggestions or []:
        ctype = (item.get("content_type") or "").lower()
        normalized = ctype
        if "practice" in ctype or ctype == "pyq":
            continue
        if "example" in ctype:
            normalized = "example"
        elif "theory" in ctype:
            normalized = "theory"
        else:
            normalized = "theory"
        if normalized in _PICKER_TYPES:
            copy = dict(item)
            copy["content_type"] = normalized
            filtered.append(copy)
    return filtered[:3]


def build_picker_flashcards(suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for item in filter_picker_suggestions(suggestions):
        content_id = item.get("content_id")
        if not content_id:
            continue
        preview = _card_front(item)
        diff = item.get("difficulty")
        stars = "★" * int(diff) if diff else ""
        cards.append(
            {
                "id": content_id,
                "card_type": item.get("content_type", "theory"),
                "front": preview,
                "back": prepare_student_markdown(
                    _truncate(item.get("solution") or item.get("core_text") or preview, 320)
                ),
                "hint": stars or None,
                "concept_id": item.get("primary_concept_id"),
                "content_id": content_id,
                "selectable": True,
            }
        )
    return cards


def build_quiz_flashcards(
    quiz_steps: Optional[List[Dict[str, Any]]] = None,
    quiz_batch_chunks: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    if quiz_batch_chunks:
        for idx, chunk in enumerate(quiz_batch_chunks, start=1):
            cards.append(
                {
                    "id": chunk.get("content_id") or f"quiz-{idx}",
                    "card_type": "quiz",
                    "front": _truncate(chunk.get("core_text") or chunk.get("question") or f"Question {idx}", 400),
                    "back": prepare_student_markdown(
                        _truncate(chunk.get("solution") or chunk.get("answer") or "", 400)
                    ),
                    "hint": chunk.get("hint"),
                    "concept_id": chunk.get("primary_concept_id") or chunk.get("concept_id"),
                    "content_id": chunk.get("content_id"),
                    "selectable": False,
                }
            )
        return cards

    for step in quiz_steps or []:
        cards.append(
            {
                "id": f"quiz-step-{step.get('step_num', len(cards) + 1)}",
                "card_type": "quiz",
                "front": _truncate(step.get("question") or "", 400),
                "back": prepare_student_markdown(_truncate(step.get("answer") or "", 400)),
                "hint": step.get("hint"),
                "concept_id": step.get("concept_id"),
                "selectable": False,
            }
        )
    return cards


def build_grading_ui(awaiting: bool) -> Optional[Dict[str, Any]]:
    if not awaiting:
        return None
    return {
        "show_correct_incorrect": True,
        "prompt": "Attempt the question above, then tap Correct or Incorrect.",
    }


def build_quiz_assessment(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if result.get("quiz_batch_mode") and result.get("quiz_batch_chunks"):
        return {
            "batch_size": int(result.get("quiz_batch_size") or len(result.get("quiz_batch_chunks") or [])),
            "require_end_assessment": True,
        }
    if result.get("quiz_steps"):
        return {
            "batch_size": len(result.get("quiz_steps") or []),
            "require_end_assessment": True,
        }
    return None


def build_proactive_card(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    node = result.get("sandbox_card")
    if isinstance(node, dict):
        return node
    concept_id = result.get("primary_concept_id") or result.get("roadmap_concept_id")
    if not concept_id:
        return None
    chunks = result.get("retrieved_chunks") or []
    preview = _truncate((chunks[0].get("core_text") if chunks else "") or concept_id.replace("_", " "), 180)
    target = result.get("target_concept_state") or {}
    return {
        "card_type": result.get("content_type_requested", "theory"),
        "title": concept_id.replace("_", " ").title(),
        "duration_min": 8,
        "proficiency_pct": int(float(target.get("proficiency_score", 0.5)) * 100),
        "weak_prereq": None,
        "preview_text": preview,
        "image_url": None,
        "actions": ["start", "skip", "why"],
        "concept_id": concept_id,
    }


def build_flashcards_payload(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    remediation = result.get("remediation_flashcards")
    if remediation:
        return {"mode": "remediation", "cards": remediation}

    suggestions = result.get("follow_up_suggestions") or []
    picker = build_picker_flashcards(suggestions)
    if picker and (result.get("agent_used") in {"follow_up", "diagnostic"} or suggestions):
        return {"mode": "picker", "cards": picker}

    quiz_cards = build_quiz_flashcards(
        quiz_steps=result.get("quiz_steps"),
        quiz_batch_chunks=result.get("quiz_batch_chunks"),
    )
    if quiz_cards and (result.get("quiz_batch_mode") or result.get("quiz_steps")):
        return {"mode": "quiz", "cards": quiz_cards}

    return None


def split_score_summary(response: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not response or "Here is where you stand after this attempt" not in response:
        return response, None
    parts = response.split("**Here is where you stand after this attempt:**", 1)
    brief = parts[0].strip() or None
    summary = "**Here is where you stand after this attempt:**" + parts[1] if len(parts) > 1 else None
    return brief, summary


def build_chat_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    response = result.get("response")
    score_summary = result.get("score_summary")
    if score_summary:
        brief = response
    else:
        brief, score_summary = split_score_summary(response)

    flashcards = build_flashcards_payload(result)
    grading_ui = build_grading_ui(bool(result.get("awaiting_graded_response")))
    quiz_assessment = build_quiz_assessment(result)
    card = build_proactive_card(result)
    target = result.get("target_concept_state") or {}
    proficiency_pct = (
        int(float(target.get("proficiency_score", 0.5)) * 100) if target else None
    )

    payload: Dict[str, Any] = {
        "response": brief or response,
        "score_summary": score_summary,
        "agent_used": result.get("agent_used"),
        "follow_up_suggestions": result.get("follow_up_suggestions") or None,
        "quiz_steps": result.get("quiz_steps") or None,
        "awaiting_graded_response": bool(result.get("awaiting_graded_response")),
        "flashcards": flashcards,
        "grading_ui": grading_ui,
        "quiz_assessment": quiz_assessment,
        "card": card,
        "mistake_brief": result.get("mistake_brief"),
        "proficiency_pct": proficiency_pct,
        "error": result.get("error"),
    }
    return payload
