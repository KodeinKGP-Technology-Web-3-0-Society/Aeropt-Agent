"""Recognise a request for one specific official paper question.

    "solve JEE Advanced 2019 paper 1 question 5"   -> (2019, 1, 5)
    "jee adv 2023 p2 q14", "2016 Paper-2 Q.7 advanced" -> (2016, 2, 7)

A named question must come back exactly — the stored official item with its official
answer — never a semantic look-alike, and when it is not in the verified set the tutor
must say so rather than offer something else as if it were that question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ADVANCED = re.compile(r"\b(jee\s*-?\s*adv(?:anced)?|advanced|iit\s*-?\s*jee)\b", re.I)
_YEAR = re.compile(r"\b(20[0-2]\d)\b")
_PAPER = re.compile(r"\b(?:paper|p)\s*[-.]?\s*(?:no\.?\s*)?([12])\b", re.I)
_QUESTION = re.compile(r"\b(?:question|ques|que|q)\s*[-.]?\s*(?:no\.?\s*|number\s*)?(\d{1,2})\b", re.I)

# Years whose official answer key was located (local_ingest/exams/catalog.py).
KEYED_YEARS = {2014, 2015, 2016, 2017, 2019, 2023, 2024, 2025, 2026}


@dataclass(frozen=True)
class Ref:
    year: int
    paper: int
    qno: int

    @property
    def content_id(self) -> str:
        return f"pyq_jee_adv_{self.year}_p{self.paper}_phy_q{self.qno:02d}"

    @property
    def label(self) -> str:
        return f"JEE Advanced {self.year} Paper {self.paper} Q{self.qno}"

    def unavailable_reason(self) -> str:
        if self.year not in KEYED_YEARS:
            if 2016 <= self.year <= 2023:
                return (f"JEE Advanced {self.year}'s full answer key could not be found; only some of its "
                        "questions are available (JEE-Bench, official answers but no question numbers), "
                        "so that exact question cannot be identified")
            return (f"no official answer key for JEE Advanced {self.year} could be found, so its "
                    "questions are not in the verified set")
        return ("it is not in the verified set — either it is not a Physics question, or it was "
                "left out because its figure or its official answer could not be read reliably")


def parse(text: str) -> Ref | None:
    if not _ADVANCED.search(text or ""):
        return None
    year, paper, question = _YEAR.search(text), _PAPER.search(text), _QUESTION.search(text)
    if not (year and paper and question):
        return None
    return Ref(int(year.group(1)), int(paper.group(1)), int(question.group(1)))


@dataclass(frozen=True)
class PaperRef:
    """A request for questions from one year (and maybe one paper), not one question:
    "a famous JEE Advanced 2016 question", "jee adv 2023 paper 2 problems"."""

    year: int
    paper: int | None

    @property
    def id_prefix(self) -> str:
        paper = f"p{self.paper}" if self.paper else "p"
        return f"pyq_jee_adv_{self.year}_{paper}"

    @property
    def label(self) -> str:
        return f"JEE Advanced {self.year}" + (f" Paper {self.paper}" if self.paper else "")


def parse_paper(text: str) -> PaperRef | None:
    """A year-level request. None when a question number is named (that is `parse`)."""
    if not _ADVANCED.search(text or "") or _QUESTION.search(text or ""):
        return None
    year = _YEAR.search(text)
    if not year:
        return None
    paper = _PAPER.search(text)
    return PaperRef(int(year.group(1)), int(paper.group(1)) if paper else None)
