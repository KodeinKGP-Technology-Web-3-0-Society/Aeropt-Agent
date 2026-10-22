"""The Answer Checker: a fresh pass over the reply before the student sees it.

Why a separate role rather than "the tutor re-reads its own work": the tutor has
just spent a whole reasoning loop committing to an approach, and its context is
full of the evidence it chose. It is the worst-placed reader of its own answer.
The checker gets the student's question, the answer, and the grounding — and
none of the reasoning that produced it.

The checker is allowed to rewrite. A verdict alone would just relocate the
problem: someone still has to fix it, and bouncing the draft back for another
full loop costs a student another round of waiting.

It is deliberately fail-open. If the checker errors, times out or returns
nonsense, the original answer goes through unchanged. A tutor that stops
answering because its reviewer is down is worse than one that occasionally ships
an unreviewed reply — and the verdict is recorded either way, so a rise in
skipped checks is visible rather than silent.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI

import config
from agents.runtime.trace import AgentTrace, TraceStep
from utils.llm import groq_complete, groq_stream

logger = logging.getLogger("jee_tutor.answer_checker")

_groq = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

#: Below this length a reply is an acknowledgement, not an explanation worth
#: paying a second model call to review.
MIN_LENGTH_TO_CHECK = 180

VERDICT_PASS = "pass"
VERDICT_REVISED = "revised"
VERDICT_FAILED = "failed"
VERDICT_SKIPPED = "skipped"


def has_claims_to_check(draft: str) -> bool:
    """True when the draft asserts numerical, physical, mathematical, or exam claims."""
    if not (draft or "").strip():
        return False
    if r"\boxed{" in draft:
        return True
    from agents.runtime.proof import needs_proof
    from agents.runtime.provenance import _EXAM_CLAIM

    if needs_proof(draft):
        return True
    if bool(_EXAM_CLAIM.search(draft)):
        return True
    return False

_CHECKER_PROMPT = """\
You are the final reviewer for a JEE tutor (covering Physics, Chemistry, and Mathematics). An answer has been drafted \
for a student. It does not reach them until you approve it.

Check it against these, in this order of importance:

1. CORRECT — is the scientific/mathematical explanation right, and does the arithmetic hold? A wrong number \
presented confidently is the worst failure here.
   Do not trust a closed form just because it looks familiar. For any claimed value of an integral, sum or \
limit, substitute the smallest case (n=1, n=2) and compute it yourself. Check that the identity really applies \
to this integrand (cos versus cosh is the classic trap). If the small case disagrees, revise.
2. RESPONSIVE — does it answer what this student actually asked, rather than a \
nearby question?
3. GROUNDED (FAIL-CLOSED FORMULAS) — Course material below was retrieved automatically and can be about a \
different situation, chapter or even subject than the question. First decide whether it addresses this exact \
problem. If it does, formulas must be consistent with it. If it does not, ignore it entirely: never revise a \
correct answer to match material about something else. With no relevant material, allow well-established, standard JEE textbook formulas, reaction mechanisms, and principles rather than rejecting the answer. Never rewrite a correct chemistry or math answer to claim that the tutor only teaches physics.
4. HONEST — is uncertainty stated plainly, rather than smoothed over?
5. TEACHING — does it explain the reasoning rather than only stating a result?
6. PITCHED — is the language right for this student's level?

Be specific and be sparing. Rewrite only what is actually wrong. A draft that is merely not how you would have phrased it is a pass, EXCEPT for formula violations which must always be revised.

Preserve exactly, when you revise:
  - all LaTeX and mathematical notation, unchanged in form;
  - all markdown image links and figure references;
  - any question put to the student at the end.
When you do revise, write plainly: no em dashes (use a full stop, comma or colon), no \
stock openers such as "Great question". Never revise a correct answer only for style.

── The student asked ────────────────────────────────────────────────────────
{query}

── What is known about this student ─────────────────────────────────────────
{learner_context}

── Course material the answer was grounded in ───────────────────────────────
{evidence}
{known_issues}
── The drafted answer ───────────────────────────────────────────────────────
{draft}

── Reply with JSON only ─────────────────────────────────────────────────────
{{
  "verdict": "pass" | "revise",
  "issues": ["each real problem, one short sentence"],
  "revised_answer": "the full corrected answer, or null when verdict is pass"
}}

If verdict is "revise", revised_answer must be the COMPLETE answer to send, not \
a diff and not a note about what to change. No text outside the JSON."""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class CheckResult:
    """Outcome of one review."""

    def __init__(
        self,
        answer: str,
        verdict: str,
        issues: Optional[List[str]] = None,
        model_used: Optional[str] = None,
    ) -> None:
        self.answer = answer
        self.verdict = verdict
        self.issues = issues or []
        self.model_used = model_used

    @property
    def was_revised(self) -> bool:
        return self.verdict == VERDICT_REVISED

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CheckResult(verdict={self.verdict!r}, issues={len(self.issues)})"


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    """Pull the JSON object out of a reply that may be wrapped in prose or fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0), strict=False)
    except json.JSONDecodeError:
        return None


def _format_evidence(
    chunks: List[Dict[str, Any]],
    web_passages: Optional[List[Dict[str, Any]]] = None,
    limit: int = 3,
) -> str:
    from retrieval.official_web import format_for_checker

    if not chunks:
        body = "(no course material was retrieved for this turn)"
    else:
        parts = []
        for chunk in chunks[:limit]:
            text = (chunk.get("core_text") or "").strip()
            parts.append(f"[{chunk.get('content_id', '?')}] {text[:500]}")
        body = "\n\n".join(parts)
    return body + format_for_checker(web_passages)


def _format_learner_context(state: Dict[str, Any]) -> str:
    bits: List[str] = []
    target = state.get("target_concept_state") or {}
    if target.get("proficiency_score") is not None:
        bits.append(f"Proficiency on this concept: {float(target['proficiency_score']):.2f}")
    if target.get("is_cold_start"):
        bits.append("This concept is new to them.")
    if state.get("grade_level"):
        bits.append(f"Grade level: {state['grade_level']}")
    if state.get("preferred_depth"):
        bits.append(f"Prefers explanations that are: {state['preferred_depth']}")
    for memory in (state.get("recalled_memories") or [])[:4]:
        fact = memory.get("fact") if isinstance(memory, dict) else str(memory)
        if fact:
            bits.append(f"Remembered: {fact}")
    return "\n".join(bits) if bits else "(nothing specific known)"


def _reasoning_kwargs() -> Dict[str, Any]:
    effort = getattr(config, "ANSWER_CHECKER_REASONING", "")
    return {"reasoning_effort": effort} if effort else {}


def _format_known_issues(state: Dict[str, Any]) -> str:
    issue = (state.get("proof_issue") or "").strip()
    if issue:
        return f"\n── Known issues detected by automated checks ───────────────────────────────\n{issue}\n"
    return ""


def check_answer(
    *,
    query: str,
    draft: str,
    state: Dict[str, Any],
    trace: Optional[AgentTrace] = None,
    timeout: Optional[float] = None,
) -> CheckResult:
    """Review a drafted answer, revising it if it needs revising.

    Returns the answer to actually send, plus the verdict. Never raises.
    """
    draft = (draft or "").strip()

    if not draft:
        return CheckResult(draft, VERDICT_FAILED, ["The draft was empty."])

    if len(draft) < MIN_LENGTH_TO_CHECK:
        # A short acknowledgement has no physics to get wrong.
        return CheckResult(draft, VERDICT_SKIPPED, ["Too short to need review."])

    if not has_claims_to_check(draft):
        return CheckResult(draft, VERDICT_SKIPPED, ["No analytical or exam claims to review."])

    if not getattr(config, "ANSWER_CHECKER_ENABLED", True):
        return CheckResult(draft, VERDICT_SKIPPED, ["Checker disabled by config."])

    prompt = _CHECKER_PROMPT.format(
        query=(query or "")[:1500],
        learner_context=_format_learner_context(state),
        evidence=_format_evidence(
            state.get("retrieved_chunks") or [],
            state.get("web_passages") or [],
        ),
        known_issues=_format_known_issues(state),
        draft=draft,
    )

    call_kwargs = _reasoning_kwargs()
    if timeout is not None:
        call_kwargs["per_call_cap"] = timeout

    step = TraceStep(kind="check", name="answer_checker")
    try:
        raw, model_used = groq_complete(
            client=_groq,
            model=getattr(config, "ANSWER_CHECKER_MODEL", config.SOCRATIC_MODEL),
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.1,
            max_tokens=2400,
            response_format={"type": "json_object"},
            **call_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — fail open, deliberately
        logger.warning("Answer checker unavailable: %s", exc)
        is_budget = "budget" in str(exc).lower() or "timeout" in str(exc).lower()
        verdict = "skipped:budget" if is_budget else VERDICT_SKIPPED
        step.ok = False
        step.error = str(exc)[:200]
        step.result_preview = verdict
        if trace:
            trace.add(step)
            trace.checker_verdict = verdict
        return CheckResult(draft, VERDICT_SKIPPED, [f"Checker unavailable: {exc}"])

    parsed = _extract_json(raw)
    if not parsed:
        logger.info("Answer checker returned unparseable output (JSON incomplete): %r — passing draft through.", raw)
        step.ok = False
        step.error = "unparseable checker output"
        step.result_preview = VERDICT_SKIPPED
        if trace:
            trace.add(step)
            trace.checker_verdict = VERDICT_SKIPPED
        return CheckResult(draft, VERDICT_SKIPPED, ["Checker output could not be parsed."])

    verdict = str(parsed.get("verdict") or "").strip().lower()
    issues = [str(i) for i in (parsed.get("issues") or []) if str(i).strip()]
    revised = parsed.get("revised_answer")

    if verdict == "revise" and isinstance(revised, str) and revised.strip():
        revised_text = revised.strip()
        # A "revision" that throws away most of the answer is more likely a
        # truncated or confused checker than a real correction. Keep the draft.
        if len(revised_text) < len(draft) * 0.4:
            logger.warning(
                "Checker revision was %d chars against a %d char draft — keeping draft.",
                len(revised_text),
                len(draft),
            )
            step.result_preview = VERDICT_SKIPPED
            step.ok = True
            if trace:
                trace.add(step)
                trace.checker_verdict = VERDICT_SKIPPED
            return CheckResult(
                draft, VERDICT_SKIPPED, ["Checker revision was implausibly short."], model_used
            )

        from agents.runtime.release import release

        released_text = release(revised_text, state)
        step.result_preview = VERDICT_REVISED
        if trace:
            trace.add(step)
            trace.checker_verdict = VERDICT_REVISED
        logger.info("Answer checker revised the draft: %s", "; ".join(issues[:3]))
        return CheckResult(released_text, VERDICT_REVISED, issues, model_used)

    step.result_preview = VERDICT_PASS
    if trace:
        trace.add(step)
        trace.checker_verdict = VERDICT_PASS
    return CheckResult(draft, VERDICT_PASS, issues, model_used)

def _unescape_json_string(s: str) -> str:
    if not s:
        return ""
    try:
        return json.loads('"' + s + '"')
    except json.JSONDecodeError:
        return s


def check_answer_streaming(
    *,
    query: str,
    draft: str,
    state: Dict[str, Any],
    trace: Optional[AgentTrace] = None,
    on_token: Optional[Callable[[str], None]] = None,
    timeout: Optional[float] = None,
) -> CheckResult:
    """Review a drafted answer with streaming support for revisions.

    Returns the answer to actually send (CheckResult), plus the verdict.
    Streams the revised text after passing through the release gate.
    """
    draft = (draft or "").strip()

    if not draft:
        return CheckResult(draft, VERDICT_FAILED, ["The draft was empty."])

    if len(draft) < MIN_LENGTH_TO_CHECK:
        return CheckResult(draft, VERDICT_SKIPPED, ["Too short to need review."])

    if not has_claims_to_check(draft):
        return CheckResult(draft, VERDICT_SKIPPED, ["No analytical or exam claims to review."])

    if not getattr(config, "ANSWER_CHECKER_ENABLED", True):
        return CheckResult(draft, VERDICT_SKIPPED, ["Checker disabled by config."])

    prompt = _CHECKER_PROMPT.format(
        query=(query or "")[:1500],
        learner_context=_format_learner_context(state),
        evidence=_format_evidence(
            state.get("retrieved_chunks") or [],
            state.get("web_passages") or [],
        ),
        known_issues=_format_known_issues(state),
        draft=draft,
    )

    call_kwargs = _reasoning_kwargs()
    if timeout is not None:
        call_kwargs["per_call_cap"] = timeout

    step = TraceStep(kind="check", name="answer_checker")
    try:
        stream = groq_stream(
            client=_groq,
            model=getattr(config, "ANSWER_CHECKER_MODEL", config.SOCRATIC_MODEL),
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.1,
            max_tokens=2400,
            response_format={"type": "json_object"},
            **call_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Answer checker stream unavailable: %s", exc)
        is_budget = "budget" in str(exc).lower() or "timeout" in str(exc).lower()
        verdict = "skipped:budget" if is_budget else VERDICT_SKIPPED
        step.ok = False
        step.error = str(exc)[:200]
        step.result_preview = verdict
        if trace:
            trace.add(step)
            trace.checker_verdict = verdict
        return CheckResult(draft, VERDICT_SKIPPED, [f"Checker unavailable: {exc}"])

    full_response = ""
    model_used = None

    try:
        for chunk, is_final in stream:
            full_response += chunk
    except StopIteration as e:
        model_used = e.value
    except Exception as exc:
        logger.warning("Answer checker stream failed during generation: %s", exc)
        is_budget = "budget" in str(exc).lower() or "timeout" in str(exc).lower()
        verdict = "skipped:budget" if is_budget else VERDICT_SKIPPED
        step.ok = False
        step.error = str(exc)[:200]
        step.result_preview = verdict
        if trace:
            trace.add(step)
            trace.checker_verdict = verdict
        return CheckResult(draft, VERDICT_SKIPPED, [f"Checker stream failed mid-way: {exc}"])

    parsed = _extract_json(full_response)
    if not parsed:
        logger.info("Answer checker returned unparseable output (JSON incomplete): %r — passing draft through.", full_response)
        step.ok = False
        step.error = "unparseable checker output"
        step.result_preview = VERDICT_SKIPPED
        if trace:
            trace.add(step)
            trace.checker_verdict = VERDICT_SKIPPED
        return CheckResult(draft, VERDICT_SKIPPED, ["Checker output could not be parsed."])

    verdict = str(parsed.get("verdict") or "").strip().lower()
    issues = [str(i) for i in (parsed.get("issues") or []) if str(i).strip()]
    revised = parsed.get("revised_answer")

    if verdict == "revise" and isinstance(revised, str) and revised.strip():
        revised_text = revised.strip()
        if len(revised_text) < len(draft) * 0.4:
            logger.warning(
                "Checker revision was %d chars against a %d char draft — keeping draft.",
                len(revised_text),
                len(draft),
            )
            step.result_preview = VERDICT_SKIPPED
            step.ok = True
            if trace:
                trace.add(step)
                trace.checker_verdict = VERDICT_SKIPPED
            return CheckResult(
                draft, VERDICT_SKIPPED, ["Checker revision was implausibly short."], model_used
            )

        from agents.runtime.release import release
        from utils.stream import emit_text

        released_text = release(revised_text, state)
        if on_token:
            emit_text(released_text, on_token)

        step.result_preview = VERDICT_REVISED
        if trace:
            trace.add(step)
            trace.checker_verdict = VERDICT_REVISED
        logger.info("Answer checker revised the draft: %s", "; ".join(issues[:3]))
        return CheckResult(released_text, VERDICT_REVISED, issues, model_used)

    step.result_preview = VERDICT_PASS
    if trace:
        trace.add(step)
        trace.checker_verdict = VERDICT_PASS
    return CheckResult(draft, VERDICT_PASS, issues, model_used)
