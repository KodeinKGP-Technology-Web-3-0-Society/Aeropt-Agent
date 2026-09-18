"""Decide whether a turn needs retrieval at all — deterministically, before any model call.

The hosted gate asks an LLM "is this a meta turn?" on every doubt turn. That costs
a network round trip to save a network round trip, and it can only say yes or no.
This gate reads the turn's shape instead and returns one of four decisions:

  retrieve   ground this turn in course material
  reuse      the material already on the turn answers it (rephrase, "explain step 2")
  skip       nothing to ground (greeting, thanks, pure arithmetic, an answer submission)
  clarify    a bare pronoun with no prior context to resolve it against

Cheap rules first, and the expensive path is *not* an LLM: when the rules are
unsure it defaults to `retrieve`, because a needless retrieval costs ~30 ms here
while a missed one costs grounding. `AEROPT_GATE_LLM=1` re-enables the classifier
for the genuinely ambiguous middle, for anyone who wants it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

GATE_LLM = os.getenv("AEROPT_GATE_LLM", "0").strip() == "1"

_GREETING_OPENERS = frozenset(
    """hi hey hello yo hii hiii namaste thanks thank thanku thankyou thx ty ok okay k
    cool nice great awesome perfect got understood bye done yes yeah yep no nope sure
    alright""".split()
)

_GREETING_TAIL = frozenset(
    """you there sir maam ma'am it that this much a lot lots so very now helped help
    helpful clear make sense sense makes good better bro buddy team again all right
    and but ok okay thanks for""".split()
)

# Talking about the last answer rather than asking something new.
_META = re.compile(
    r"\b(shorter|shorten|simpler|simplify|explain (that|this|it|again|step)|rephrase|"
    r"repeat|in (hindi|simpler|short)|summar(y|ise|ize)|what do you mean|didn'?t (get|understand)|"
    r"why (is|was) (that|this|it|my answer)|where did i go wrong|i don'?t follow|"
    r"say (that|it) again|last (step|line|part)|previous (answer|step|reply)|"
    r"too (long|fast|complex|hard to follow))\b",
    re.IGNORECASE,
)

# Arithmetic the calculator tool answers; no course material involved.
_PURE_MATH = re.compile(r"^[\s\d()+\-*/^%.,=xX×÷√]+\??$")

# The student is answering a question we asked, not asking a new one.
_ANSWER_SUBMISSION = re.compile(
    r"^(\(?[a-d]\)?|option\s*[1-4a-d]|[1-4]|[-+]?\d+(\.\d+)?\s*[a-zA-Z/^0-9]*|"
    r"(my )?answer( is|:)?\s*.{0,40})$",
    re.IGNORECASE,
)

_NEW_TOPIC = re.compile(
    r"\b(what|why|how|derive|prove|define|state|explain|difference|formula|when|which|"
    r"teach|learn|start|revise|practice|practise|problem|question|pyq|example|numerical|"
    r"solve|calculate|find|show)\b",
    re.IGNORECASE,
)

# "another one", "next question" — a new item, not the one already in hand, so this
# retrieves *and* demands novelty against what the student has already been served.
_ANOTHER = re.compile(
    r"\b(another|one more|next (one|question|problem|example|pyq)|"
    r"different (one|question|problem)|again but different|some more|more (questions|problems|examples))\b",
    re.IGNORECASE,
)

_MATH_PREFIX = re.compile(
    r"^(what('s| is)|whats|calculate|compute|evaluate|solve|find|how much is|tell me)\s+",
    re.IGNORECASE,
)

_FOLLOW_UP_DEIXIS = re.compile(
    r"^(and|but|so|also|ok(ay)? )?\s*(what about|how about|then|that|this|it|those|these|"
    r"the same|another|one more|next)\b",
    re.IGNORECASE,
)


@dataclass
class Decision:
    action: str  # retrieve | reuse | skip | clarify
    reason: str
    content_type: str = "theory"
    confidence: float = 1.0
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_retrieval(self) -> bool:
        return self.action == "retrieve"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "content_type": self.content_type,
            "confidence": round(self.confidence, 3),
            **self.notes,
        }


def infer_content_type(query: str) -> str:
    lowered = (query or "").lower()
    if re.search(r"\b(pyq|past year|previous year|jee (main|advanced) question|asked in jee)\b", lowered):
        return "pyq"
    if re.search(r"\b(practice|practise|drill|quiz|test me|give me (a |some )?(question|problem)s?|"
                 r"problems?|exercise|numericals?)\b", lowered):
        return "practice"
    if re.search(r"\b(worked example|example|show me how|walk me through|solved)\b", lowered):
        return "example"
    return "theory"


def decide(
    query: str,
    *,
    recent_turns: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    forced: bool = False,
) -> Decision:
    state = state or {}
    turns = recent_turns or []
    text = (query or "").strip()
    content_type = infer_content_type(text)

    if forced:
        return Decision("retrieve", "the agent asked for material explicitly", content_type)

    if not text:
        return Decision("skip", "empty message", content_type)

    # "thanks, that helped!" and "hi there" are acknowledgements with a tail, not
    # questions. Matching the whole string against a fixed list missed both, and the
    # turn then retrieved a full pack of material for a thank-you.
    tokens = re.findall(r"[a-zA-Z']+", text.lower())
    if tokens and tokens[0] in _GREETING_OPENERS:
        if all(tok in _GREETING_OPENERS | _GREETING_TAIL for tok in tokens) and len(tokens) <= 7:
            return Decision("skip", "greeting or acknowledgement, nothing to ground", content_type)

    awaiting = bool(state.get("awaiting_graded_response"))
    if awaiting and _ANSWER_SUBMISSION.match(text) and len(text) < 80:
        return Decision(
            "skip",
            "this is the student's answer to the question we served, not a new question",
            content_type,
            notes={"grading_turn": True},
        )

    bare_math = _MATH_PREFIX.sub("", text).strip().rstrip("?").strip()
    if bare_math and _PURE_MATH.match(bare_math) and len(bare_math) < 60:
        return Decision("skip", "pure arithmetic — the calculator answers this", content_type)

    has_context = bool(state.get("retrieved_chunks"))

    if _ANOTHER.search(text):
        return Decision(
            "retrieve",
            "asking for a further item, so it must not repeat what they have already seen",
            content_type,
            notes={"novelty_required": True},
        )

    if _META.search(text):
        if has_context:
            return Decision("reuse", "asking about the previous answer; material already in hand", content_type)
        return Decision("skip", "asking about the previous answer, which is in the transcript", content_type)

    # A follow-up that points back ("and this one?") rather than naming a topic
    # ("what about friction on an incline") is answerable from what we already have.
    # The test is whether anything survives stripping the deixis and the stopwords:
    # a leftover noun phrase means a new topic, so it retrieves.
    words = re.findall(r"\w+", text)
    if has_context and len(words) <= 8 and _FOLLOW_UP_DEIXIS.match(text) and not _residual_topic(text):
        return Decision("reuse", "short follow-up on the material already retrieved", content_type)

    if len(words) <= 2 and not _NEW_TOPIC.search(text):
        if not turns:
            return Decision("clarify", "too short to resolve and no conversation to lean on", content_type)
        return Decision("retrieve", "short but the topic can be resolved from context", content_type, 0.6)

    if GATE_LLM and 3 <= len(words) <= 6 and has_context:
        llm = _llm_is_meta(text, turns)
        if llm is True:
            return Decision("reuse", "classifier read this as a follow-up on the last answer", content_type, 0.7)

    return Decision("retrieve", "a substantive question about course material", content_type)


_STOPWORDS = frozenset(
    """a an and the of for to in on at is are was were do does did can could would should
    about what how why when which that this it those these them then so but also ok okay
    about same another next one more please tell me my your i you we us again bit little
    much very just now yes no thanks sir ma'am""".split()
)


def _residual_topic(text: str) -> str:
    """What the student named beyond pointing at the last answer."""
    stripped = _FOLLOW_UP_DEIXIS.sub("", text, count=1)
    rest = [w for w in re.findall(r"[a-zA-Z]+", stripped.lower()) if w not in _STOPWORDS]
    return " ".join(rest) if len(rest) >= 2 else ""


def _llm_is_meta(query: str, turns: list[dict[str, Any]]) -> bool | None:
    """Optional classifier for the ambiguous middle. Off unless AEROPT_GATE_LLM=1."""
    try:
        from agents.retrieval_gate import is_meta_repair_turn

        return bool(is_meta_repair_turn(query, turns))
    except Exception:  # noqa: BLE001 — the gate must never fail a turn
        return None
