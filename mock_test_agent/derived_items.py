"""Turn a worked solution into a single-correct question.

The correct option is always a result printed in the solution. A model may
point at that ending and suggest other choices. It may not invent a number,
rewrite the question, or supply a result that is not in the text. If the
quoted ending is not actually in the solution, the question is left out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aeropt.mock_derived")

_SYSTEM = (
    "You copy the final result from a worked solution. Do not calculate a new value "
    "and do not rewrite the question. Reply with JSON only: "
    '{"quote":"the exact ending copied from the solution","value":"the single final result as printed","distractors":["wrong value","wrong value","wrong value"]}. '
    "The quote must be copied from the solution. The value must appear inside that quote. "
    "If the solution gives two final results, a range, or no final result, return empty strings and an empty distractor list."
)

_NUM = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*[x×]\s*10\^?\{?[+-]?\d+\}?)?"
_NUM_RE = re.compile(_NUM)
_UNIT_RE = re.compile(
    r"^(?P<num>[+-]?(?:\d+(?:\.\d+)?|\.\d+))"
    r"(?:\s*[x×]\s*10\^?\{?(?P<pow>[+-]?\d+)\}?)?"
    r"(?P<unit>(?:\s*(?:\\[A-Za-z]+|[%A-Za-zΩµμ°/^*·.\-])+)?)\s*\.?\s*$"
)


def _numbers(text: str) -> List[str]:
    return _NUM_RE.findall(text or "")


def _plain(text: str) -> str:
    text = re.sub(r"\\(?:text|mathrm|operatorname)\{([^}]*)\}", r"\1", text or "")
    text = re.sub(r"\\[A-Za-z]+", " ", text)
    text = text.replace("$", " ").replace("{", " ").replace("}", " ")
    text = text.replace("\\", " ")
    return re.sub(r"\s+", " ", text).strip()


def _clean_tail(solution: str) -> str:
    text = re.sub(r"\(Rewritten from.*?\)\s*$", "", solution or "", flags=re.S | re.I)
    return _plain(text)


def _last_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.])\s+", text.strip())
    return parts[-1].strip() if parts else ""


def _one_result(fragment: str) -> Optional[str]:
    """One printed number on the right of the last equals, or the whole short line."""
    fragment = fragment.strip(" .;")
    if not fragment:
        return None
    if "=" in fragment or "≈" in fragment:
        fragment = re.split(r"=|≈", fragment)[-1].strip(" .;")
    if re.search(r"\b(?:and|or)\b", fragment, re.I):
        return None
    found = _numbers(fragment)
    if len(found) != 1:
        return None
    number = found[0]
    if number in {"0", "0.0", "+0", "-0"}:
        return None
    after = fragment.split(number, 1)[1].strip(" .;")
    # A unit may follow. A word of ordinary prose may not.
    if re.search(r"[A-Za-z]{5,}", after):
        return None
    unit = after[:24].strip()
    return f"{number} {unit}".strip()


def extract_final_value(solution: str) -> Optional[str]:
    """The single final result printed at the end of a solution, or None.

    The number and unit are taken from that ending. Nothing is recalculated.
    """
    tail = _clean_tail(solution)
    if len(tail) < 8:
        return None
    sentence = _last_sentence(tail[-900:])
    if re.search(r"\b(?:and|or)\b", sentence, re.I) and len(_numbers(sentence)) >= 2:
        return None
    if "=" not in sentence and "≈" not in sentence and len(sentence.split()) > 14:
        return None
    return _one_result(sentence)


def _parse_printed(text: str) -> Optional[Tuple[float, int, str]]:
    """(value, decimal places, unit suffix). Decimal places follow the printed token."""
    match = _UNIT_RE.match(text.replace(" ", "")) or _UNIT_RE.match(text)
    if not match:
        return None
    raw = match.group("num")
    places = len(raw.split(".")[1]) if "." in raw else 0
    value = float(raw)
    power = match.group("pow")
    if power:
        value *= 10 ** int(power)
        places = 0
    unit = match.group("unit") or ""
    unit = unit.replace("\\text", "").replace("{", "").replace("}", "")
    return value, places, unit.strip()


def _format_like(value: float, places: int, unit: str) -> str:
    if places:
        printed = f"{value:.{places}f}"
    elif abs(value - round(value)) < 1e-9:
        printed = str(int(round(value)))
    else:
        printed = f"{value:.4g}"
    return f"{printed} {unit}".strip()


def _distractors(correct: str) -> Optional[List[str]]:
    parsed = _parse_printed(correct)
    if not parsed:
        return None
    value, places, unit = parsed
    if value == 0:
        return None
    candidates = [value * 2, value / 2, value * -1, value * 1.1, value + (1 if abs(value) >= 1 else 0.1)]
    out: List[str] = []
    seen = {correct.replace(" ", "")}
    for item in candidates:
        if item == 0 or abs(item - value) / max(abs(value), 1e-9) < 0.02:
            continue
        text = _format_like(item, places, unit)
        key = text.replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) == 3:
            return out
    return None


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def accept_quoted_result(solution: str, quote: str, value: str) -> Optional[str]:
    """Keep a model result only when the solution itself contains that quote and value."""
    quote = (quote or "").strip()
    value = (value or "").strip()
    if not solution or len(quote) < 4 or not value or len(value) > 80:
        return None
    ending = solution[-1800:]
    if _compact(quote) not in _compact(ending):
        return None
    if _compact(value) not in _compact(quote):
        return None
    if _compact(value) not in _compact(solution):
        return None
    if re.search(r"\b(?:and|or)\b", quote, re.I) and len(_numbers(quote)) >= 2:
        return None
    return value


def _pack(correct: str, wrong: List[str], content_id: str) -> Optional[Dict[str, Any]]:
    slot = int(hashlib.sha256(content_id.encode()).hexdigest(), 16) % 4
    labels = ["A", "B", "C", "D"]
    texts = list(wrong)
    texts.insert(slot, correct)
    if len(texts) != 4 or texts.count(correct) != 1:
        return None
    return {
        "question_type": "single_correct",
        "options": [{"label": label, "text": text} for label, text in zip(labels, texts)],
        "answer": {"kind": "letters", "letters": labels[slot]},
        "derived_from_solution": True,
    }


def _safe_distractors(correct: str, proposed: Optional[List[str]] = None) -> Optional[List[str]]:
    out: List[str] = []
    seen = {_compact(correct)}
    for item in proposed or []:
        text = str(item or "").strip()
        key = _compact(text)
        if not text or len(text) > 80 or key in seen:
            continue
        if _compact(correct) and _compact(correct) in key:
            continue
        seen.add(key)
        out.append(text)
        if len(out) == 3:
            return out
    return _distractors(correct)


def make_single_correct(solution: str, content_id: str) -> Optional[Dict[str, Any]]:
    """Options and a letter key. The correct option text is the printed result."""
    correct = extract_final_value(solution)
    if not correct:
        return None
    wrong = _safe_distractors(correct)
    if not wrong:
        return None
    return _pack(correct, wrong, content_id)


def _json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def from_model_reply(solution: str, content_id: str, reply: str) -> Optional[Dict[str, Any]]:
    """Build a question from a model reply, or None when the reply is not in the solution."""
    data = _json_object(reply)
    value = accept_quoted_result(solution, str(data.get("quote") or ""), str(data.get("value") or ""))
    if not value:
        return None
    proposed = data.get("distractors") if isinstance(data.get("distractors"), list) else []
    wrong = _safe_distractors(value, [str(item) for item in proposed])
    if not wrong:
        return None
    made = _pack(value, wrong, content_id)
    if made:
        made["quote"] = str(data.get("quote") or "")[:500]
    return made


def load_cached(content_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Previously checked solutions. Empty when the cache table is not there yet."""
    ids = [item for item in content_ids if item]
    if not ids:
        return {}
    try:
        from db.supabase_client import db
    except Exception:
        return {}
    found: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 200):
        batch = ids[start:start + 200]
        try:
            resp = db.client.table("mock_solution_items").select(
                "content_id,status,value,options,answer"
            ).in_("content_id", batch).execute()
        except Exception:
            return found
        for row in resp.data or []:
            if isinstance(row, dict) and row.get("content_id"):
                found[row["content_id"]] = row
    return found


def store_cached(content_id: str, made: Optional[Dict[str, Any]]) -> None:
    row: Dict[str, Any] = {
        "content_id": content_id,
        "status": "accepted" if made else "rejected",
        "value": "",
        "options": None,
        "answer": None,
    }
    if made:
        letter = (made.get("answer") or {}).get("letters")
        value = next((opt["text"] for opt in made.get("options") or [] if opt.get("label") == letter), "")
        row["value"] = value
        row["options"] = made.get("options")
        row["answer"] = made.get("answer")
    try:
        from db.supabase_client import db
        db.client.table("mock_solution_items").upsert(row, on_conflict="content_id").execute()
    except Exception:
        logger.warning("could not cache a derived solution item", exc_info=True)


def from_cache(solution: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if row.get("status") != "accepted":
        return None
    options = row.get("options") or []
    answer = row.get("answer") or {}
    letter = answer.get("letters")
    correct = next((opt.get("text") for opt in options if opt.get("label") == letter), "")
    if not correct or _compact(str(correct)) not in _compact(solution):
        return None
    return {
        "question_type": "single_correct",
        "options": options,
        "answer": answer,
        "derived_from_solution": True,
    }


def read_with_model(solution: str, content_id: str) -> Optional[Dict[str, Any]]:
    """Ask a model to point at the printed final result. Reject anything not in the solution."""
    made = make_single_correct(solution, content_id)
    if made:
        return made
    ending = (solution or "")[-1800:].strip()
    if len(ending) < 40:
        return None
    try:
        from mock_test_agent import settings as config
        from utils.llm import complete_text

        reply, _model = complete_text(
            client=None,
            model=config.MOCK_DEBRIEF_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": ending},
            ],
            fallback_models=config.MOCK_DEBRIEF_FALLBACK_MODELS,
            temperature=0,
            max_tokens=400,
            lane="background",
        )
    except Exception:
        logger.warning("could not read a solution ending for %s", content_id, exc_info=True)
        return None
    return from_model_reply(solution, content_id, reply or "")
