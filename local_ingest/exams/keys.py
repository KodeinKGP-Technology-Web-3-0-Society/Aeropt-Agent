"""Official answer keys, read from the key document's own text layer.

No model is involved here, and nothing here sees the extracted questions: the key
is the ground truth that extraction is checked against, so it must not depend on
extraction. Each key document is the full paper with an "Answer: …" line under
every question; the question text before it is kept as a fingerprint so the join
in assemble.py can prove it is pairing the right two things.

Formats seen across years:
    Answer: B        Answer: (C)       Answer: A, D       Answer Q5: AC
    Answer: 665      Answer: [2520]    Answer: [1.15 to 1.25]    Answer Q12: [3.9 TO 4.1]
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# "Q.12 ..." (2017+), or "12. At time ..." (2014: a number, a full stop, a capital).
_Q = re.compile(r"^\s*(?:Q\.?\s*(\d{1,2})\b|(\d{1,2})\.\s+(?=[A-Z(]))\s*(.*)$")
# "Answer: B", "Answer Q5: AC", "Answer (C) and (D)" (2014), "Ans for Q.8: (6)" (2017,
# several on one line). Section instructions also start "Answer to each question…";
# a match only counts if it parses as an answer (see parse).
_ANSWER = re.compile(r"^\s*Answer\s*(?:Q\.?\s*(\d{1,2}))?\s*:?\s*(.+?)\s*$", re.I)
_ANS_FOR = re.compile(r"Ans(?:wer)?\s*for\s*Q\.?\s*(\d{1,2})\s*:\s*(.+?)(?=\s{2,}Ans|\s*$)", re.I)
# 2019: "Question No.   Physics   Chemistry   Mathematics", then one row per number.
_TABLE_HEAD = re.compile(r"Question\s*No\.?\s+(\w+)\s+(\w+)\s+(\w+)", re.I)
_TABLE_ROW = re.compile(r"^\s*(\d{1,2})\s{2,}(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s*$")
_RANGE = re.compile(r"^[\[(]?\s*(-?\d+(?:\.\d+)?)\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)\s*[\])]?$", re.I)
_NUMBER = re.compile(r"^[\[(]?\s*(-?\d+(?:\.\d+)?)\s*[\])]?$")
_LETTERS = re.compile(r"^[A-D]+$")
_OR = re.compile(r"\s+or\s+", re.I)
_BONUS = re.compile(r"bonus|marks? (?:to|for) all|all candidates|dropped", re.I)


@dataclass
class KeyEntry:
    qno: int
    raw: str
    kind: str = ""            # letters | integer | range | any_of | bonus | unreadable
    letters: str = ""         # "AD" for A, D
    value: float | None = None
    low: float | None = None
    high: float | None = None
    context: str = ""         # the key document's own copy of the question opening
    alternatives: list["KeyEntry"] = field(default_factory=list)  # any_of: each one is accepted


@dataclass
class KeyBlock:
    """One subject's answers, in document order (subjects restart at Q.1)."""
    entries: dict[int, KeyEntry] = field(default_factory=dict)
    order: list[int] = field(default_factory=list)  # Q numbers as they appear, keyed or not
    subject: str = ""  # set when the key itself names the subject (tables)


def normalise(raw: str) -> KeyEntry:
    entry = KeyEntry(qno=0, raw=raw)
    text = raw.strip().rstrip(".*").strip()  # 2019 marks some ranges with a footnote star
    if _BONUS.search(text):
        entry.kind = "bonus"
        return entry
    m = _RANGE.match(text)
    if m:
        entry.kind, entry.low, entry.high = "range", float(m.group(1)), float(m.group(2))
        return entry
    m = _NUMBER.match(text)
    if m:
        entry.kind, entry.value = "integer" if "." not in m.group(1) else "range", float(m.group(1))
        if entry.kind == "range":
            entry.low = entry.high = entry.value
        return entry
    letters = re.sub(r"[\s(),&\[\]]|and", "", text, flags=re.I).upper()
    if _LETTERS.match(letters):
        entry.kind = "letters"
        entry.letters = "".join(sorted(set(letters)))
        return entry
    parts = _OR.split(text)
    if len(parts) > 1:
        # A final-key ruling that accepts more than one answer ("A or B").
        alternatives = [normalise(part) for part in parts]
        if all(a.kind in {"letters", "integer", "range"} for a in alternatives):
            entry.kind, entry.alternatives = "any_of", alternatives
            return entry
    entry.kind = "unreadable"
    return entry


def text_layer(pdf: Path) -> str:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True)
    return out.stdout


def parse(text: str) -> list[KeyBlock]:
    table = parse_table(text)
    if table:
        return table
    blocks: list[KeyBlock] = []
    current_q = 0
    context: list[str] = []

    def record(n: int, raw: str) -> None:
        entry = normalise(raw)
        if entry.kind == "unreadable":
            return  # "Answer to each question will be evaluated…" is not an answer
        prior = blocks[-1].entries.get(n)
        if prior is not None and prior.kind != "unreadable":
            return
        entry.qno = n
        entry.context = " ".join(context)[:400] if n == current_q else ""
        blocks[-1].entries[n] = entry

    for line in text.splitlines():
        several = list(_ANS_FOR.finditer(line))
        if several and blocks:
            for m in several:
                record(int(m.group(1)), m.group(2))
            continue
        q = _Q.match(line)
        if q:
            n = int(q.group(1) or q.group(2))
            if n == 1 or not blocks or n <= current_q:
                blocks.append(KeyBlock())
            current_q = n
            blocks[-1].order.append(n)
            context = [q.group(3)]
            continue
        a = _ANSWER.match(line)
        if a and blocks:
            record(int(a.group(1)) if a.group(1) else current_q, a.group(2))
            continue
        if blocks and len(" ".join(context)) < 400:
            context.append(line.strip())
    return blocks


def parse_table(text: str) -> list[KeyBlock]:
    """A key printed as a table with one answer column per subject (2019)."""
    head = _TABLE_HEAD.search(text)
    if not head:
        return []
    subjects = [head.group(i).capitalize() for i in (1, 2, 3)]
    blocks = [KeyBlock(subject=s) for s in subjects]
    for line in text[head.end():].splitlines():
        row = _TABLE_ROW.match(line)
        if not row:
            continue
        n = int(row.group(1))
        for block, raw in zip(blocks, row.groups()[1:]):
            entry = normalise(raw)
            entry.qno = n
            block.order.append(n)
            block.entries[n] = entry
    return [b for b in blocks if b.entries]


def fold(text: str) -> set[str]:
    """Words for fuzzy comparison: maths-italic letters folded, short tokens dropped."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"\\[a-z]+", " ", text)  # LaTeX commands in the extracted stem
    return {w for w in re.findall(r"[a-z]{3,}", text)}


def similarity(a: str, b: str) -> float:
    wa, wb = fold(a), fold(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))
