"""H.C. Verma rows extracted outside this pipeline (.local-run/CLASS XII&Xi/<class>/<chapter>/
content_*.csv), checked against the book's own PDF pages before any of it is used.

    .venv/bin/python -m local_ingest.hcverma        # verify -> .local-run/sources/hcverma/hcverma.jsonl
    make push                                       # fold the verified rows in

The rows were produced by a model reading the book, and measuring them showed three different
things inside one file: worked-example questions are near-verbatim (median 96 % of their
3-word phrases are printed in the book); worked-example solutions are mostly rewritten (median
40 %); theory is mostly paraphrased (median 42 %). So each part is held to its own test:

  question   kept only if its phrases are printed in the chapter's PDF (>= 50 %)
  solution   kept if verbatim, or if its final distinctive number (not a small integer, not data
             from the question) matches — within 2 % — a number in the book's own solution to that
             example (located by the question's text); otherwise the question is kept without a
             stored solution. Symbolic-only answers are not confirmable this way. (A weaker check
             once confirmed a solution to a different problem: -3GM/2a for the book's -3GM/a.)
  theory     kept if verbatim, or a close paraphrase: >= 85 % of its words and every number
             printed in the book
  any row    maths must render; a question that cites a figure must carry a description

A chapter without its source PDF cannot be checked and is not ingested (Oscillations).
Licence: H.C. Verma, Concepts of Physics (Bharati Bhawan) — sponsor material.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from utils.latex_guidelines import find_latex_issues

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".local-run" / "CLASS XII&Xi"
OUT = ROOT / ".local-run" / "sources" / "hcverma"
LICENSE = "H.C. Verma, Concepts of Physics (Bharati Bhawan); sponsor material"

# Folder -> (syllabus chapter, JEE unit). Folder names are the extractor's, not the book's.
CHAPTERS: dict[str, tuple[str, str]] = {
    "Gravitation": ("gravitation", "Gravitation"),
    "Kinetic Theory of Gases": ("the_kinetic_theory_of_gases", "Kinetic Theory of Gases"),
    "Mechanical Properties Of Fluids": ("fluid_mechanics", "Properties of Solids and Liquids"),
    "Mechanical Properties of Solids": ("static_equilibrium_and_elasticity", "Properties of Solids and Liquids"),
    "Oscillations": ("oscillations", "Oscillations and Waves"),
    "Thermal Properties of Matter": ("temperature_and_heat", "Properties of Solids and Liquids"),
    "Thermodynamics": ("the_first_law_of_thermodynamics", "Thermodynamics"),
    "Waves": ("waves", "Oscillations and Waves"),
    "Capacitors & Electrostatic Energy": ("capacitance", "Electrostatics"),
    "Current Electricity": ("current_and_resistance", "Current Electricity"),
    "Electric Charges, Fields & Electrostatics": ("electric_charges_and_fields", "Electrostatics"),
    "Magnetism & Magnetic Materials": ("sources_of_magnetic_fields", "Magnetic Effects of Current and Magnetism"),
    "Moving Charges & Magnetic Fields": ("magnetic_forces_and_fields", "Magnetic Effects of Current and Magnetism"),
}

MIN_VERBATIM = 0.5
MIN_PARAPHRASE_WORDS = 0.85
NUMBER_TOLERANCE = 0.02
_FIGURE_REF = re.compile(r"\b(figure|fig\.|shown|diagram)\b", re.I)


def _norm(text: str) -> str:
    return (text or "").replace("·", ".").replace("⋅", ".").replace("∙", ".")


def _seq(text: str) -> list[str]:
    text = re.sub(r"\$[^$]*\$|\\[a-zA-Z]+|[{}]", " ", _norm(text)).lower()
    return re.findall(r"[a-z]{3,}", text)


def _shingles(words: list[str]) -> set[str]:
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def _numbers(text: str) -> list[float]:
    text = re.sub(r"\\[a-zA-Z]+|[{}^_]", " ", _norm(text))
    return [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", text)]


class Book:
    """One chapter's source PDF(s): phrases, words and numbers, for checking rows against."""

    def __init__(self, pdfs: list[Path]):
        raw = "\n".join(subprocess.run(["pdftotext", "-raw", str(p), "-"], capture_output=True, text=True).stdout
                        for p in pdfs)
        self.flat = re.sub(r"\s+", " ", _norm(raw))
        self.words_seq = _seq(raw)
        self.phrases = _shingles(self.words_seq)
        self.words = set(re.findall(r"[a-z]{4,}", self.flat.lower()))
        self.numbers = set(_numbers(self.flat))

    def verbatim(self, text: str) -> float | None:
        sh = _shingles(_seq(text))
        return sum(s in self.phrases for s in sh) / len(sh) if len(sh) >= 6 else None

    def word_share(self, text: str) -> float:
        words = re.findall(r"[a-z]{4,}", re.sub(r"\$[^$]*\$|\\[a-zA-Z]+", " ", _norm(text)).lower())
        return sum(w in self.words for w in words) / len(words) if words else 1.0

    def solution_region(self, question: str) -> str:
        """The book text right after the question — where its own printed solution is.
        Found by the question's last 5-word phrase that the book prints exactly once."""
        if not hasattr(self, "_index"):
            matches = list(re.finditer(r"[a-z]{3,}", self.flat.lower()))
            self._words_at = [m.start() for m in matches]
            self._index: dict[str, list[int]] = {}
            words = [m.group() for m in matches]
            for i in range(len(words) - 4):
                self._index.setdefault(" ".join(words[i:i + 5]), []).append(i)
        words = _seq(question)
        for start in range(len(words) - 5, -1, -1):  # from the end of the question backwards
            hits = self._index.get(" ".join(words[start:start + 5]), [])
            if len(hits) == 1:
                begin = self._words_at[hits[0]]
                return self.flat[begin: begin + 1200]  # its own solution, not the next example
        return ""


def _close(a: float, pool: set[float]) -> bool:
    return any(abs(a - b) <= NUMBER_TOLERANCE * max(abs(a), abs(b), 1e-9) for b in pool)


def verify_row(r: dict[str, str], book: Book) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    kind = r["content_type"]
    core, solution = r["core_text"].strip(), r["solution"].strip()
    figure = r.get("diagram_description_core", "").strip()

    if find_latex_issues(core) or find_latex_issues(solution):
        return None, ["maths would not render"]
    if _FIGURE_REF.search(core) and r.get("has_diagram") == "true" and not figure:
        return None, ["refers to a figure that has no description"]

    record: dict[str, Any] = {"id": r["content_id"], "kind": "theory" if kind == "Theory" else "example",
                              "question": core, "figure": figure, "solution": "", "solution_status": "",
                              "page": r.get("page_number", ""), "concept_hint": (r.get("primary_concept_id") or "").replace("_", " ")}
    if kind == "Theory":
        v = book.verbatim(core)
        if v is not None and v >= MIN_VERBATIM:
            record["solution_status"] = "verbatim"
            return record, reasons
        numbers = _numbers(core)
        if book.word_share(core) >= MIN_PARAPHRASE_WORDS and all(_close(n, book.numbers) for n in numbers):
            record["solution_status"] = "paraphrase"
            return record, reasons
        return None, [f"theory not printed in the book ({(v or 0):.0%} verbatim) and not a close paraphrase"]

    v = book.verbatim(core)
    if v is not None and v < MIN_VERBATIM:
        return None, [f"question not printed in the book ({v:.0%} of its phrases)"]
    sv = book.verbatim(solution)
    if solution and sv is not None and sv >= MIN_VERBATIM:
        record.update(solution=solution, solution_status="book")
        return record, reasons
    # The final answer must be a *distinctive* number: small integers (3 in "-3GM/2a") occur
    # everywhere and once confirmed a solution to a different problem; data copied from the
    # question proves nothing. A symbolic-only answer cannot be confirmed this way.
    given = set(_numbers(core))
    finals = [n for n in _numbers(solution[-300:])
              if n not in given and (n != int(n) or (n >= 13 and n not in (100, 1000)))]
    region = book.solution_region(core)
    if solution and finals and region and _close(finals[-1], set(_numbers(region))):
        record.update(solution=solution, solution_status="rewritten; final answer matches the book")
        return record, reasons
    record["kind"] = "exercise"  # the question stands; its solution could not be confirmed
    reasons.append("solution not confirmed by the book: kept as a question without a stored solution")
    return record, reasons


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    summary: Counter = Counter()
    for csv_path in sorted(SOURCE.glob("*/*/content_*.csv")):
        folder = csv_path.parent
        if folder.name not in CHAPTERS:
            dropped.append({"chapter": folder.name, "ref": "*", "reason": "chapter not mapped"})
            continue
        chapter_id, unit = CHAPTERS[folder.name]
        pdfs = sorted(folder.glob("*.pdf"))
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8", errors="replace")))
        if not pdfs:
            dropped += [{"chapter": folder.name, "ref": r["content_id"], "reason": "no source PDF to check against"} for r in rows]
            summary["no source PDF"] += len(rows)
            continue
        book = Book(pdfs)
        for r in rows:
            record, reasons = verify_row(r, book)
            for reason in reasons:
                dropped.append({"chapter": folder.name, "ref": r["content_id"], "reason": reason})
            if record is None:
                summary["dropped"] += 1
                continue
            record.update(chapter=folder.name, chapter_id=chapter_id, unit=unit,
                          source_pdf=", ".join(p.name for p in pdfs))
            kept.append(record)
            summary[f"{record['kind']}: {record['solution_status'] or 'question only'}"] += 1
    (OUT / "hcverma.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
    (OUT / "report.json").write_text(json.dumps({"summary": summary, "dropped": dropped}, indent=1, ensure_ascii=False))
    for k, v in summary.most_common():
        print(f"{v:4}  {k}")
    print(f"\n{len(kept)} verified H.C. Verma records -> {(OUT / 'hcverma.jsonl').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
