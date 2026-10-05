"""Build every retrievable item from the sources, and map each one onto the syllabus.

Sources, all automatic:
  OpenStax CNXML   theory sections, glossary definitions, chapter summaries,
                   worked examples (with solutions), end-of-section exercises
  HuggingFace PYQs JEE Main/Advanced past questions with solutions
  JEE-Bench        JEE Advanced questions with verified gold answers

Concept mapping is embedding-based, never a keyword table: a chunk is scored
against the concepts of the module and chapter it came from (for book content) or
of the JEE unit its topic label names (for questions). Book structure gives a
small, correct candidate set, so the mapping does not depend on the embedding
model being strong — it only has to rank a handful of sibling concepts.

Every item carries `source_book` + `source_ref` + `license`, so an answer can cite
where a claim came from. That citation trail is the anti-hallucination mechanism:
an ungrounded claim has nothing to point at.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from local_ingest.jee_syllabus import alias_index
from local_ingest.reference_notes import NOTES, REFERENCE_BOOK, REFERENCE_LICENSE
from local_ingest.syllabus import Chapter, Concept, norm, slug
from retrieval import embedder

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / ".local-run" / "sources"

TYPE_THEORY = "Theory"
TYPE_EXAMPLE = "Solved Example"
TYPE_PRACTICE = "Practice Question"
TYPE_PYQ = "PYQ"

EXAM_BOTH = "Jee main and Advanced"
EXAM_MAIN = "Jee main"
EXAM_ADV = "Jee Advanced"

OPENSTAX_BOOK = "OpenStax University Physics"
OPENSTAX_LICENSE = "CC BY-NC-SA 4.0"

THEORY_CHARS = 1500
THEORY_MIN = 220


# Figure descriptions (cnxml.py) are for the reader: they carry the values a
# "shown below" problem needs. They are not matching signal — "a baseball player
# catching the ball" pulled a kinematics example toward "who won the cricket world
# cup" — so embeddings, keyword search and fingerprints all see the text without them.
_FIGURE_TEXT = re.compile(r"\[Figure: [^\]]*\]")


def match_text(text: str) -> str:
    return _FIGURE_TEXT.sub(" ", text or "")


# ── text helpers ──────────────────────────────────────────────────────────────
# Source-specific LaTeX quirks. These are transcription errors in the source data,
# not judgement calls: \mathcal takes uppercase letters only, so `\mathcal{l}` is
# invalid LaTeX for the length symbol the author meant.
_LATEX_FIXUPS = (
    (re.compile(r"\\mathcal\{l\}"), r"\\ell "),
    (re.compile(r"\\mathcal\{([a-z])\}"), r"\\mathrm{\1}"),
    (re.compile(r"\\text\{(sin|cos|tan|cot|sec|csc|log|ln|exp|lim|max|min)\}"), r"\\\1 "),
    (re.compile("[\u3008\u27e8]"), r"\\langle "),
    (re.compile("[\u3009\u27e9]"), r"\\rangle "),
    (re.compile("\u210f"), r"\\hbar "),
    (re.compile("\u2113"), r"\\ell "),
    (re.compile("\u221e"), r"\\infty "),
    # `^\circ` with no group swallows the next letter: "0^\circC" parses \circC as one
    # undefined control sequence, so every temperature in the corpus failed to render.
    (re.compile(r"\^\\circ(?![a-zA-Z{])"), r"^{\\circ}"),
    (re.compile(r"\^\\circ([A-Za-z])"), r"^{\\circ}\\text{\1}"),
    (re.compile("[\u00ba\u02da]"), r"^{\\circ}"),
    (re.compile("\u2206"), r"\\Delta "),
    (re.compile("\u00bd"), r"\\tfrac{1}{2}"),
    (re.compile("\u00bc"), r"\\tfrac{1}{4}"),
    (re.compile("\u00be"), r"\\tfrac{3}{4}"),
    (re.compile("[\u200b\u200c\u200d\ufeff]"), ""),
    (re.compile("\u00af"), ""),
    # A second superscript on the same base aborts the KaTeX parse.
    (re.compile(r"(\^\{[^{}]*\})\s*\^"), r"\1{}^"),
    # `E_{\text{int}}_{i}` is a double subscript, which aborts the parse. One level of
    # nesting has to be allowed for, since the first subscript is usually \text{...}.
    (re.compile(r"(_\{(?:[^{}]|\{[^{}]*\})*)\}_\{((?:[^{}]|\{[^{}]*\})*\})"), r"\1,\2"),
    # The PYQ source opens multi-line `aligned` blocks with a single `$`, which is
    # display maths written with an inline delimiter.
    (re.compile(r"(?<!\$)\$(\s*\\begin\{(?:aligned|align|gather|cases)\})"), r"$$\1"),
    (re.compile(r"(\\end\{(?:aligned|align|gather|cases)\}\s*)\$(?!\$)"), r"\1$$"),
    # Half-delimited source markup: "V=1m$^3" opens a span on a superscript, and
    # "$$$b=K$" is a triple delimiter. Both come from the PYQ .docx conversion.
    (re.compile(r"\${3,}"), "$$"),
    (re.compile(r"(?<=[A-Za-z0-9)])\$(?=[\^_])"), ""),
    # A delimiter right inside a group: "30^{$\circ$}" — the `$` was never meant as maths.
    (re.compile(r"\{\$(?!\$)"), "{"),
    (re.compile(r"(?<!\$)\$\}"), "}"),
    # KaTeX has no \mspace; the PYQ source uses it purely as thin space.
    (re.compile(r"\\mspace\{[^{}]*\}"), r"\\,"),
    # "\muT" is one undefined macro, not \mu followed by T.
    (re.compile(
        r"\\(mu|nu|pi|rho|sigma|tau|phi|chi|psi|omega|theta|alpha|beta|gamma|delta|epsilon|lambda|kappa|eta|zeta|iota|xi)"
        r"([A-Z])"
    ), r"\\\1 \2"),
)

# A `$` inside a math span closes it early and the rest of the line becomes prose in
# maths mode. The PYQ source does this a lot: "30^{$\circ$}".
_NESTED_DOLLAR_RE = re.compile(r"(?<!\\)\$([^$\n]*?)(?<!\\)\$")

# Bare LaTeX sitting in source prose with no delimiters at all ("each equal to
# 30^{\circ} are placed"). The frontend has heuristics for this, but they have to
# guess where the maths ends; at ingest the construct itself is visible, so wrapping
# it here is exact and the frontend never has to improvise.
_BARE_LATEX_RE = re.compile(
    r"(?<![$\\])("
    r"\^\{\\?[A-Za-z0-9\\ ]{1,12}\}"
    r"|\\(?:frac|sqrt|vec|hat|bar|overline|times|cdot|approx|leq|geq|neq|pm|infty|Delta|Omega|omega|alpha|beta|gamma|theta|lambda|mu|pi|rho|sigma|tau|phi|circ|degree)"
    r"(?:\{[^{}]{0,40}\}){0,2}"
    r")"
)


def _strip_nested_dollars(text: str) -> str:
    """Remove `$` that sits inside an already-open math span."""

    def fix(match: "re.Match[str]") -> str:
        body = match.group(1)
        if "$" not in body:
            return match.group(0)
        return "$" + body.replace("$", "") + "$"

    return _NESTED_DOLLAR_RE.sub(fix, text)


def _wrap_bare_latex(text: str) -> str:
    """Delimit LaTeX constructs that arrived in prose without any `$`."""
    out: list[str] = []
    pos = 0
    in_math = False
    for match in re.finditer(r"(?<!\\)\$\$?", text):
        in_math = not in_math
    # Only bother when the document is not already fully delimited maths.
    pieces = re.split(r"(\$\$[\s\S]*?\$\$|(?<!\\)\$[^$\n]*?(?<!\\)\$)", text)
    for piece in pieces:
        if piece and (piece.startswith("$") or piece.startswith("$$")):
            out.append(piece)
            continue
        out.append(_BARE_LATEX_RE.sub(lambda m: "$" + m.group(1) + "$", piece or ""))
    return "".join(out)


def _neutralize_math(text: str) -> str:
    """Strip every math delimiter, keeping the formula text readable.

    For the handful of chunks whose source markup cannot be made sound (a `.docx`
    conversion that split a brace group across two delimiters, a truncated formula),
    shipping delimiters guarantees a visible defect downstream. Without them the
    formula still reads as text, and nothing can mis-pair.
    """
    guarded = text.replace("\\$", "\uE008")
    guarded = guarded.replace("$$", " ").replace("$", "")
    # Restore the literal dollar *escaped*. Unescaping it here re-created exactly the
    # odd-delimiter problem this function exists to remove.
    return guarded.replace("\uE008", "\\$")


def latex_safe(text: str) -> str:
    """Make stored text renderable: fix source quirks, escape money, degrade the rest.

    Ingest is the right place for this. The corpus is read by the model as ground
    truth *and* quoted back to the student, so a formula that cannot render is a
    defect in the data — not something the frontend should be patching per request.
    """
    from utils.latex_guidelines import (
        balance_dollar_delimiters,
        escape_currency_dollars,
        harden_math_spans,
    )

    out = text or ""
    # KaTeX is configured for `$`/`$$` only, so TeX's own delimiters have to be
    # converted here rather than left for every downstream reader to handle.
    out = re.sub(r"\\\[(.+?)\\\]", lambda m: "$$\n" + m.group(1).strip() + "\n$$", out, flags=re.DOTALL)
    out = re.sub(r"\\\((.+?)\\\)", lambda m: "$" + m.group(1).strip() + "$", out, flags=re.DOTALL)
    for pattern, repl in _LATEX_FIXUPS:
        out = pattern.sub(repl, out)
    out = _strip_nested_dollars(out)
    out = _wrap_bare_latex(out)
    out = escape_currency_dollars(out)
    # `plain`: at ingest a bad span loses its delimiters and stays as text. Code
    # fences belong in chat output, not woven through stored prose.
    out = harden_math_spans(out, mode="plain")
    # Whatever is still unpaired gets escaped, so a stored chunk can never open a math
    # span that runs to the end of the page.
    out = balance_dollar_delimiters(out)
    # Final check on the whole chunk, not span by span: if anything is still wrong,
    # this chunk's markup is beyond mechanical repair, so it ships as plain text.
    from utils.latex_guidelines import find_latex_issues

    if find_latex_issues(out) or _line_parity_broken(out):
        return _neutralize_math(out)
    return out


_DISPLAY_BLOCK_RE = re.compile(r"\$\$[\s\S]*?\$\$")
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")


def _line_parity_broken(text: str) -> bool:
    """True when a line leaves an inline span open.

    Inline maths cannot cross a newline, so an odd count on one line means the span
    runs on and swallows the prose after it. Display blocks legitimately span lines and
    are removed before counting.
    """
    stripped = _DISPLAY_BLOCK_RE.sub(" ", text or "")
    return any(
        len(_UNESCAPED_DOLLAR_RE.findall(line)) % 2 == 1 for line in stripped.split("\n")
    )


def clean(text: str) -> str:
    text = (text or "").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fingerprint(text: str) -> str:
    """Stable hash of a question's content words — catches the same question twice.

    Two items with the same fingerprint are the same problem even if one source
    reformatted it, so the novelty filter can refuse a near-duplicate, not just a
    literal repeat of the same content_id.
    """
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", (text or "").lower())
    words = [w for w in words if len(w) > 2][:120]
    shingles = sorted({" ".join(words[i : i + 3]) for i in range(max(0, len(words) - 2))})
    digest = hashlib.sha1(" | ".join(shingles[:40]).encode()).hexdigest()
    return digest[:16]


def token_len(text: str) -> int:
    return max(1, len(re.findall(r"\S+", text or "")))


def split_theory(text: str, budget: int = THEORY_CHARS) -> list[str]:
    """Paragraph-aligned chunks: never split a derivation mid-sentence."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        if size and size + len(para) > budget:
            chunks.append("\n\n".join(buf))
            buf, size = [], 0
        if len(para) > budget * 1.8:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            cur: list[str] = []
            for sent in sentences:
                if sum(len(s) for s in cur) + len(sent) > budget and cur:
                    chunks.append(" ".join(cur))
                    cur = []
                cur.append(sent)
            if cur:
                buf.append(" ".join(cur))
                size += sum(len(s) for s in cur)
            continue
        buf.append(para)
        size += len(para)
    if buf:
        chunks.append("\n\n".join(buf))
    return [c for c in chunks if len(c) >= THEORY_MIN] or ([text] if len(text or "") >= THEORY_MIN else [])


_EXERCISE_DIFFICULTY = {
    "review-conceptual-questions": 2,
    "review-problems": 3,
    "review-additional-problems": 4,
    "review-challenge": 5,
    "check-understanding": 2,
    "": 3,
}


def _item(
    *,
    content_id: str,
    content_type: str,
    core_text: str,
    solution: str,
    chapter: Chapter | None,
    heading: str,
    source_book: str,
    source_ref: str,
    license_: str,
    difficulty: int,
    question_type: str,
    target_exam: str,
    module_id: str = "",
    candidate_scope: tuple[str, ...] = (),
) -> dict[str, Any]:
    core_text = latex_safe(clean(core_text))
    solution = latex_safe(clean(solution))
    is_question = content_type in {TYPE_EXAMPLE, TYPE_PRACTICE, TYPE_PYQ}
    return {
        "content_id": content_id,
        "content_type": content_type,
        "subject": "Physics",
        "chapter_id": chapter.chapter_id if chapter else "",
        "chapter": chapter.title if chapter else "",
        "unit": (chapter.jee_unit or chapter.unit) if chapter else "",
        "in_syllabus": bool(chapter.in_syllabus) if chapter else True,
        "heading": heading,
        "core_text": core_text,
        "solution": solution,
        "source_book": source_book,
        "source_ref": source_ref,
        "license": license_,
        "difficulty": difficulty,
        "question_type": question_type,
        "target_exam": target_exam,
        "has_solution": bool(solution),
        "is_question": is_question,
        "fingerprint": fingerprint(match_text(core_text)) if is_question else fingerprint(match_text(core_text)[:400]),
        "token_len": token_len(core_text) + token_len(solution),
        "module_id": module_id,
        "candidate_scope": list(candidate_scope),
        "concept_id": "",
        "secondary_concepts": [],
        "mapping_score": 0.0,
    }


# ── OpenStax items ────────────────────────────────────────────────────────────
def openstax_items(chapters: list[Chapter], modules: dict[str, Any]) -> list[dict[str, Any]]:
    by_module: dict[str, Chapter] = {mid: ch for ch in chapters for mid in ch.module_ids}
    items: list[dict[str, Any]] = []

    for mid, mod in modules.items():
        ch = by_module.get(mid)
        if ch is None:
            continue
        ref = f"{ch.title} · {mod.title} ({mid})"

        for si, sec in enumerate(mod.sections):
            heading = " › ".join(sec.get("path") or [sec.get("title") or mod.title])
            for ci, body in enumerate(split_theory(sec["text"])):
                items.append(
                    _item(
                        content_id=f"th_{mid}_{si}_{ci}",
                        content_type=TYPE_THEORY,
                        core_text=f"{heading}\n\n{body}",
                        solution="",
                        chapter=ch,
                        heading=heading,
                        source_book=OPENSTAX_BOOK,
                        source_ref=ref,
                        license_=OPENSTAX_LICENSE,
                        difficulty=1,
                        question_type="theory",
                        target_exam=EXAM_BOTH,
                        module_id=mid,
                    )
                )

        for gi, entry in enumerate(mod.glossary):
            items.append(
                _item(
                    content_id=f"def_{mid}_{gi}",
                    content_type=TYPE_THEORY,
                    core_text=f"{entry['term']}\n\n{entry['term'].capitalize()}: {entry['meaning']}",
                    solution="",
                    chapter=ch,
                    heading=f"Definition · {entry['term']}",
                    source_book=OPENSTAX_BOOK,
                    source_ref=ref,
                    license_=OPENSTAX_LICENSE,
                    difficulty=1,
                    question_type="definition",
                    target_exam=EXAM_BOTH,
                    module_id=mid,
                )
            )

        if mod.key_concepts and len(mod.key_concepts) > 200:
            items.append(
                _item(
                    content_id=f"sum_{mid}",
                    content_type=TYPE_THEORY,
                    core_text=f"{mod.title} — key concepts\n\n{mod.key_concepts[:4000]}",
                    solution="",
                    chapter=ch,
                    heading=f"{mod.title} · key concepts",
                    source_book=OPENSTAX_BOOK,
                    source_ref=ref,
                    license_=OPENSTAX_LICENSE,
                    difficulty=1,
                    question_type="summary",
                    target_exam=EXAM_BOTH,
                    module_id=mid,
                )
            )

        for ei, ex in enumerate(mod.examples):
            if len(ex["solution"]) < 60:
                continue
            items.append(
                _item(
                    content_id=f"ex_{mid}_{ei}",
                    content_type=TYPE_EXAMPLE,
                    core_text=ex["question"],
                    solution=ex["solution"],
                    chapter=ch,
                    heading=ex["title"] or f"{mod.title} · worked example",
                    source_book=OPENSTAX_BOOK,
                    source_ref=ref,
                    license_=OPENSTAX_LICENSE,
                    difficulty=2,
                    question_type="worked_example",
                    target_exam=EXAM_BOTH,
                    module_id=mid,
                )
            )

        for xi, exc in enumerate(mod.exercises):
            if len(exc["question"]) < 40:
                continue
            kind = exc.get("kind") or ""
            items.append(
                _item(
                    content_id=f"pr_{mid}_{xi}",
                    content_type=TYPE_PRACTICE,
                    core_text=exc["question"],
                    solution=exc["solution"],
                    chapter=ch,
                    heading=f"{mod.title} · practice",
                    source_book=OPENSTAX_BOOK,
                    source_ref=ref,
                    license_=OPENSTAX_LICENSE,
                    difficulty=_EXERCISE_DIFFICULTY.get(kind, 3),
                    question_type="conceptual" if "conceptual" in kind else "numeric",
                    target_exam=EXAM_BOTH,
                    module_id=mid,
                )
            )
    return items


# ── PYQ items ─────────────────────────────────────────────────────────────────
_DIFFICULTY_LABEL = {"easy": 2, "medium": 3, "moderate": 3, "hard": 4, "tough": 5}


def _pyq_difficulty(label: str, exam: str) -> int:
    base = _DIFFICULTY_LABEL.get((label or "").strip().lower())
    if base:
        return base
    return 4 if exam == EXAM_ADV else 3


# Hindi typed in the Kruti Dev font survives extraction as Latin gibberish
# ("dh le:i jLlh ds"), interleaved with the English of bilingual papers.
_KRUTI_DEV = re.compile(r"(?<![A-Za-z])(?:dks|ds|gS|gSA|esa|fd|vkSj|ls|gks|tkrk|fy,|dk|dh|ij|;g|rFkk|Kkr)(?![A-Za-z])")
# A worked solution that lost its equation images reads "I = = 0.4A".
_LOST_EQUATION = re.compile(r"=\s*=|\[IMAGE\]")

PYQ_SOURCE_NOTE = (
    "coaching test paper, labelled {exam} level in the source dataset; "
    "not verified as an official NTA paper"
)


def _is_mojibake(text: str) -> bool:
    return len(_KRUTI_DEV.findall(text or "")) >= 2


def hf_pyq_items(chapter_lookup: dict[str, Chapter]) -> list[dict[str, Any]]:
    """Questions from the HF JEE set — only the ones a text-only tutor can answer.

    The set is drawn from coaching test papers (P-01 … Ph-28, a teacher's
    Advanced sheet), not NTA papers, so it is cited as such: a tutor saying "this
    came in JEE Main 2021" about one of them would be inventing provenance.

    Dropped, because serving them makes the model invent what is missing:
      * the question has figures (`question_images`): circuits, graphs, block
        arrangements the dataset only references by file name;
      * an option is an image, so the choices cannot be read;
      * the text is interleaved with Kruti Dev mojibake.
    A worked solution that lost its images is cut back to the answer key.
    """
    aliases = alias_index()
    files = [
        ("jee_main_physics_train.jsonl", EXAM_MAIN),
        ("jee_main_physics_test.jsonl", EXAM_MAIN),
        ("jee_adv_physics_train.jsonl", EXAM_ADV),
        ("jee_adv_physics_test.jsonl", EXAM_ADV),
    ]
    items: list[dict[str, Any]] = []
    seen_fp: set[str] = set()
    for name, default_exam in files:
        path = SOURCES / "hf" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            question = clean(row.get("question") or "")
            solution = clean(row.get("solution") or "")
            if len(question) < 40 or "[IMAGE]" in question or row.get("question_images"):
                continue
            if _is_mojibake(question):
                continue
            raw_options = [clean(row.get(f"option_{i}") or "") for i in range(1, 5)]
            if any(raw_options) and any(not o or "[IMAGE]" in o for o in raw_options):
                continue
            if not any(raw_options) and str(row.get("question_type") or "") in {"single_correct", "multiple_correct"}:
                continue  # a multiple-choice question whose options were all images: unanswerable
            if row.get("solution_images") or _LOST_EQUATION.search(solution) or _is_mojibake(solution):
                # The working is incomplete; the answer key below is still exact.
                solution = ""
            options = [f"({i}) {o}" for i, o in enumerate(raw_options, start=1) if o]
            core = question if not options else question + "\n\n" + "\n".join(options)
            answer_bits = []
            if row.get("correct_option") not in (None, "", 0):
                answer_bits.append(f"Correct option: {row['correct_option']}")
            if row.get("numerical_answer"):
                answer_bits.append(f"Numerical answer: {row['numerical_answer']}")
            if row.get("answer"):
                answer_bits.append(f"Answer: {row['answer']}")
            if answer_bits:
                solution = (solution + "\n\n" + "\n".join(answer_bits)).strip()
            if not solution:
                continue  # an unanswerable question cannot ground anything

            exam_label = str(row.get("exam") or "")
            exam = EXAM_ADV if "advanced" in exam_label.lower() else default_exam
            exam_name = "JEE Advanced" if exam == EXAM_ADV else "JEE Main"
            topic = str(row.get("topic") or "")
            subtopic = str(row.get("subtopic") or "")
            unit = ""
            for label in (topic, subtopic):
                hit = aliases.get(norm(label))
                if hit:
                    unit = hit[1]
                    break
            qid = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(row.get("question_id") or "q"))
            item = _item(
                content_id=f"pyq_{slug(path.stem)}_{qid}",
                content_type=TYPE_PYQ,
                core_text=core,
                solution=solution,
                chapter=None,
                heading=f"{exam_name}-level test-paper question · {topic or 'Physics'}",
                source_book=f"Coaching test paper {row.get('source_paper') or ''}".strip(),
                source_ref=f"{row.get('question_id') or ''} ({PYQ_SOURCE_NOTE.format(exam=exam_name)})",
                license_="coaching test paper",
                difficulty=_pyq_difficulty(str(row.get("difficulty") or ""), exam),
                question_type=str(row.get("question_type") or "single_correct"),
                target_exam=exam,
                candidate_scope=(unit,) if unit else (),
            )
            item["topic_label"] = topic or subtopic
            answer = None
            if row.get("correct_option") not in (None, "", 0):
                letters = "".join(sorted({"ABCD"[int(c) - 1] for c in re.findall(r"[1-4]", str(row["correct_option"]))}))
                answer = {"kind": "letters", "letters": letters} if letters else None
            elif row.get("numerical_answer") not in (None, ""):
                from local_ingest.structure import answer_dict
                answer = answer_dict(str(row["numerical_answer"]))
            item["structured"] = {
                "stem": question, "figure": "",
                "options": [{"label": "ABCD"[i], "text": o} for i, o in enumerate(raw_options)] if any(raw_options) else None,
                "qtype": item["question_type"], "answer": answer, "built_by": "source",
            }
            if item["fingerprint"] in seen_fp:
                continue
            seen_fp.add(item["fingerprint"])
            items.append(item)
    return items


NCERT_LICENSE = "NCERT textbook (© NCERT); local development use"


def _split_theory(text: str, limit: int = THEORY_CHARS) -> list[str]:
    """Paragraph-boundary chunks of at most ~`limit` characters."""
    chunks, current = [], ""
    for para in re.split(r"\n{2,}", text):
        if current and len(current) + len(para) > limit:
            chunks.append(current.strip())
            current = ""
        current += para + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


def ncert_items(chapter_lookup: dict[str, Chapter]) -> list[dict[str, Any]]:
    """NCERT theory, worked examples and exercises from local_ingest/ncert.

    Records were validated at extraction (figures described, maths renders, examples
    carry their printed solutions). Concepts are mapped within the record's JEE unit.
    """
    path = SOURCES / "ncert" / "ncert.jsonl"
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ch = chapter_lookup.get(row["chapter_id"])
        ref = f"Ch {row['chapter']} · {row.get('section') or ''}".strip(" ·")
        common = dict(chapter=ch, source_book=row["book"], license_=NCERT_LICENSE,
                      target_exam=EXAM_BOTH, candidate_scope=(row["unit"],))
        if row["kind"] == "theory":
            heading = f"{row['chapter']} › {row['section']}"
            for k, part in enumerate(_split_theory(row["text"])):
                item = _item(content_id=f"{row['id']}_{k}", content_type=TYPE_THEORY,
                             core_text=f"{heading}\n\n{part}", solution="", heading=heading,
                             source_ref=ref, difficulty=1, question_type="theory", **common)
                item["concept_hint"] = row.get("section") or ""
                items.append(item)
        else:
            example = row["kind"] == "example"
            label = f"{'Example' if example else 'Exercise'} {row['number']}"
            items.append(_item(
                content_id=row["id"], content_type=TYPE_EXAMPLE if example else TYPE_PRACTICE,
                core_text=row["question"], solution=row.get("solution") or "",
                heading=f"NCERT {label} · {row['chapter']}", source_ref=f"{ref} · {label}",
                difficulty=2 if example else 3, question_type="worked_example" if example else "exercise",
                **common,
            ))
    return items


HCVERMA_LICENSE = "H.C. Verma, Concepts of Physics (Bharati Bhawan); sponsor material"


def hcverma_items(chapter_lookup: dict[str, Chapter]) -> list[dict[str, Any]]:
    """H.C. Verma rows verified against the book by local_ingest/hcverma.py.

    Theory is labelled verbatim or summary; a worked example carries a solution only when it
    is the book's own or reaches the book's answer; otherwise it is a question without one.
    """
    # hcverma.jsonl: the sponsor's CSV rows; hcverma_pdf.jsonl: chapters read from the PDFs.
    lines = [line for name in ("hcverma.jsonl", "hcverma_pdf.jsonl")
             if (SOURCES / "hcverma" / name).exists()
             for line in (SOURCES / "hcverma" / name).read_text(encoding="utf-8").splitlines()]
    items: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        ch = chapter_lookup.get(row["chapter_id"])
        figure = f"\n\n[Figure: {row['figure']}]" if row.get("figure") else ""
        common = dict(chapter=ch, source_book="H.C. Verma, Concepts of Physics", license_=HCVERMA_LICENSE,
                      target_exam=EXAM_BOTH, candidate_scope=(row["unit"],))
        page = f", p. {row['page']}" if row.get("page") else ""
        if row["kind"] == "theory":
            label = "" if row["solution_status"] == "verbatim" else " (summary)"
            item = _item(content_id=f"hcv_{row['id']}", content_type=TYPE_THEORY,
                         core_text=row["question"] + figure, solution="",
                         heading=f"H.C. Verma · {row['chapter']}{label}",
                         source_ref=f"{row['chapter']}{page}{label}", difficulty=2, question_type="theory", **common)
        else:
            solved = row["kind"] == "example"
            note = ("" if row["solution_status"] == "book" else
                    "\n\n(Rewritten from H.C. Verma's worked solution; its final answer matches the book.)")
            item = _item(content_id=f"hcv_{row['id']}", content_type=TYPE_EXAMPLE if solved else TYPE_PRACTICE,
                         core_text=row["question"] + figure, solution=(row["solution"] + note) if solved else "",
                         heading=f"H.C. Verma worked example · {row['chapter']}",
                         source_ref=f"{row['chapter']}{page}", difficulty=3,
                         question_type="worked_example" if solved else "exercise", **common)
        item["concept_hint"] = row.get("concept_hint") or ""
        items.append(item)
    return items


OFFICIAL_LICENSE = "official exam paper (jeeadv.ac.in) with official final answer key"
_QTYPE_LABEL = {
    "single_correct": "one correct option", "multiple_correct": "one or more correct options",
    "matching": "matching (one correct option)", "integer": "integer answer", "numeric": "numerical answer",
}


def official_exam_items(chapter_lookup: dict[str, Chapter] | None = None) -> list[dict[str, Any]]:
    """Verified official papers from local_ingest/exams (`python -m local_ingest.exams`).

    The answer is the official final key, joined to the question only after the
    checks in exams/assemble.py; there is no worked solution, so the solution field
    says so rather than pretending to one.
    """
    path = SOURCES / "exams" / "jee_advanced.jsonl"
    if not path.exists():
        return []
    # exams/solve.py: worked solutions kept only when they reach the official answer.
    solved: dict[str, str] = {}
    sol_path = SOURCES / "exams" / "jee_advanced_solutions.jsonl"
    if sol_path.exists():
        for line in sol_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line) if line.strip() else {}
            if rec.get("solution"):
                solved[rec["id"]] = rec["solution"]
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        parts = []
        if row.get("paragraph"):
            parts.append(row["paragraph"])
        parts.append(row["stem"])
        if row.get("figure"):
            parts.append(f"[Figure: {row['figure']}]")
        if row.get("options"):
            parts.append("\n".join(f"({k}) {v}" for k, v in row["options"].items()))
        label = f"JEE Advanced {row['year']} Paper {row['paper']}"
        item = _item(
            content_id=f"pyq_{row['id']}",
            content_type=TYPE_PYQ,
            core_text="\n\n".join(parts),
            solution=(
                f"Official answer: {row['answer']} (official final answer key, {label})\n\n"
                "Worked solution (written by Aeropt; its final answer was checked against the "
                f"official key):\n\n{solved[row['id']]}"
                if row["id"] in solved else
                f"Official answer: {row['answer']}\n\n"
                f"(Official final answer key, {label}. No worked solution is stored — "
                "derive it with the student and check it reaches this answer.)"
            ),
            # exams/classify.py filed it under a chapter; concepts are then mapped within it.
            chapter=(chapter_lookup or {}).get(row.get("chapter_id") or ""),
            heading=f"{label} · Q{row['qno']} · "
                    + ("matrix match (a row may match several entries)" if row.get("answer_kind") == "matrix"
                       else _QTYPE_LABEL.get(row['qtype'], row['qtype'])),
            source_book=label,
            source_ref=f"Q{row['qno']} (official paper and final answer key)",
            license_=OFFICIAL_LICENSE,
            difficulty=4,
            question_type=row["qtype"],
            target_exam=EXAM_ADV,
        )
        item["topic_label"] = ""
        from local_ingest.structure import answer_dict
        item["structured"] = {
            "stem": "\n\n".join(x for x in (row.get("paragraph"), re.sub(r"^\s*Q\.?\s*\d+\s*", "", row["stem"])) if x),
            "figure": row.get("figure") or "",
            "options": [{"label": k, "text": v} for k, v in (row.get("options") or {}).items()] or None,
            "qtype": row["qtype"], "answer": answer_dict(row["answer_raw"]), "built_by": "source",
        }
        # Paragraph questions open with the same shared passage; fingerprint the
        # question itself, or the dedupe step treats paragraph-mates as one problem.
        own = row["stem"] + " " + " ".join((row.get("options") or {}).values())
        item["fingerprint"] = fingerprint(latex_safe(clean(own)))
        items.append(item)
    return items


def jeebench_items(skip_papers: set[str] = frozenset()) -> list[dict[str, Any]]:
    """`skip_papers`: "JEE Adv 2023 Paper 1"-style names already ingested from the official paper."""
    path = SOURCES / "jeebench" / "test.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    type_map = {"MCQ": "single_correct", "MCQ(multiple)": "multiple_correct", "Integer": "integer", "Numeric": "numeric"}
    for seq, row in enumerate(rows):
        if (row.get("subject") or "").lower() != "phy" or row.get("description") in skip_papers:
            continue
        question = clean(row.get("question") or "")
        gold = clean(str(row.get("gold") or ""))
        if len(question) < 40 or not gold:
            continue
        item = _item(
            content_id=f"pyq_jeebench_{seq}_{row.get('index')}",
            content_type=TYPE_PYQ,
            core_text=question,
            solution=f"Answer: {gold}\n\n(Verified answer key; work the solution through with the student.)",
            chapter=None,
            heading=f"JEE Advanced PYQ · {row.get('description') or ''}".strip(" ·"),
            source_book=str(row.get("description") or "JEE Advanced past paper"),
            source_ref=f"JEE-Bench #{row.get('index')} ({row.get('description') or 'JEE Advanced'})",
            license_="past exam paper",
            difficulty=4,
            question_type=type_map.get(str(row.get("type")), "single_correct"),
            target_exam=EXAM_ADV,
        )
        item["topic_label"] = ""
        from local_ingest.structure import answer_dict
        item["answer_struct"] = answer_dict(gold)
        items.append(item)
    return items


# ── reference notes ───────────────────────────────────────────────────────────
def reference_items(chapter_lookup: dict[str, Chapter]) -> list[dict[str, Any]]:
    """The authored notes in reference_notes.py, for topics no source teaches."""
    items: list[dict[str, Any]] = []
    for chapter_id, title, text in NOTES:
        ch = chapter_lookup.get(chapter_id)
        if ch is None:
            raise ValueError(f"reference note {title!r} names unknown chapter {chapter_id!r}")
        items.append(
            _item(
                content_id=f"ref_{slug(title)}",
                content_type=TYPE_THEORY,
                core_text=f"{title}\n\n{text.strip()}",
                solution="",
                chapter=ch,
                heading=title,
                source_book=REFERENCE_BOOK,
                source_ref=title,
                license_=REFERENCE_LICENSE,
                difficulty=1,
                question_type="theory",
                target_exam=EXAM_BOTH,
            )
        )
    return items


# ── concept mapping ───────────────────────────────────────────────────────────
def _concept_text(c: Concept) -> str:
    alias = " ; ".join(c.aliases[:3])
    return f"{c.name}. {c.chapter}. {alias}. {c.definition[:400]}"


def map_concepts(
    items: list[dict[str, Any]],
    concepts: list[Concept],
    chapters: list[Chapter],
    *,
    min_score: float = 0.20,
    batch: int = 512,
) -> tuple[np.ndarray, np.ndarray]:
    """Assign primary + secondary concepts to every item. Returns (item_vecs, concept_vecs)."""
    concept_vecs = embedder.encode([_concept_text(c) for c in concepts])
    index_of = {c.concept_id: i for i, c in enumerate(concepts)}
    by_module: dict[str, list[int]] = {}
    by_chapter: dict[str, list[int]] = {}
    by_unit: dict[str, list[int]] = {}
    for i, c in enumerate(concepts):
        by_module.setdefault(c.module_id, []).append(i)
        by_chapter.setdefault(c.chapter_id, []).append(i)
        if c.in_syllabus:
            by_unit.setdefault(c.unit, []).append(i)
    in_scope = [i for i, c in enumerate(concepts) if c.in_syllabus]
    chapter_of = {ch.chapter_id: ch for ch in chapters}

    texts = [
        (it["heading"] + "\n" + match_text(it["core_text"])[:1200] + "\n" + match_text(it["solution"])[:300]).strip()
        for it in items
    ]
    item_vecs = np.zeros((len(items), concept_vecs.shape[1]), dtype=np.float32)
    for start in range(0, len(texts), batch):
        item_vecs[start : start + batch] = embedder.encode(texts[start : start + batch])

    # A printed section title that names a concept outright ("3.4 OHM'S LAW") is better
    # evidence than embedding similarity, which filed NCERT's Ohm's-law section under drift
    # velocity. Names only: aliases include module titles shared by a whole module.
    def _words(text: str) -> str:
        # Concept names use a curly apostrophe ("Ohm’s law"), book headings a straight one.
        text = re.sub(r"['’`]", "", re.sub(r"\s*\([^)]*\)", "", (text or "").lower()))
        return " " + " ".join(re.findall(r"[a-z0-9]+", text)) + " "

    concept_words = [_words(c.name) for c in concepts]

    def named_in(hint: str, pool: list[int]) -> int | None:
        target = _words(hint)
        best, best_len = None, 0
        for j in pool:
            name = concept_words[j]
            if len(name) > 5 and name in target and len(name) > best_len:
                best, best_len = j, len(name)
        return best

    for i, it in enumerate(items):
        module_rows = by_module.get(it.get("module_id") or "", [])
        chapter_rows = by_chapter.get(it.get("chapter_id") or "", [])
        scope_rows: list[int] = []
        for unit in it.get("candidate_scope") or []:
            scope_rows.extend(by_unit.get(unit, []))
        candidates = module_rows or scope_rows or chapter_rows or in_scope
        # A module's own concepts are the best answer when they exist, but a
        # section can teach a concept defined in a sibling module, so the chapter
        # stays in the candidate pool.
        pool = sorted(set(candidates) | set(chapter_rows)) if module_rows else sorted(set(candidates))
        if not pool:
            pool = in_scope
        sims = concept_vecs[pool] @ item_vecs[i]
        order = np.argsort(-sims)
        best = pool[order[0]]
        named = named_in(it.get("concept_hint") or "", pool) if it.get("concept_hint") else None
        if named is not None:
            best = named
        it["concept_id"] = concepts[best].concept_id
        it["mapping_score"] = float(sims[order[0]]) if named is None else 1.0
        secondary = [
            concepts[pool[j]].concept_id
            for j in order[1:4]
            if float(sims[j]) >= min_score and pool[j] != best
        ]
        it["secondary_concepts"] = secondary
        if not it["chapter_id"]:
            concept = concepts[best]
            it["chapter_id"] = concept.chapter_id
            it["chapter"] = concept.chapter
            it["unit"] = concept.unit
            ch = chapter_of.get(concept.chapter_id)
            it["in_syllabus"] = bool(ch.in_syllabus) if ch else True
    return item_vecs, concept_vecs


def build_items(syllabus: dict[str, Any]) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray]:
    chapters: list[Chapter] = syllabus["chapters"]
    concepts: list[Concept] = syllabus["concepts"]
    chapter_lookup = {ch.chapter_id: ch for ch in chapters}

    items = openstax_items(chapters, syllabus["modules"])
    items += hf_pyq_items(chapter_lookup)
    items += ncert_items(chapter_lookup)
    items += hcverma_items(chapter_lookup)
    official = official_exam_items(chapter_lookup)
    items += official
    items += jeebench_items({f"JEE Adv {it['source_book'].split()[2]} Paper {it['source_book'].split()[-1]}" for it in official})
    items += reference_items(chapter_lookup)

    # Cross-source duplicate questions: same problem, keep the one with a solution.
    best_by_fp: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for it in items:
        if not it["is_question"]:
            ordered.append(it)
            continue
        fp = it["fingerprint"]
        prior = best_by_fp.get(fp)
        if prior is None:
            best_by_fp[fp] = it
            ordered.append(it)
            continue
        if len(it["solution"]) > len(prior["solution"]):
            ordered[ordered.index(prior)] = it
            best_by_fp[fp] = it

    by_id: dict[str, dict[str, Any]] = {}
    unique: list[dict[str, Any]] = []
    for it in ordered:
        if it["content_id"] in by_id:
            continue
        by_id[it["content_id"]] = it
        unique.append(it)

    from local_ingest.structure import attach
    counts = attach(unique)
    print(f"  question structure: {counts}")
    item_vecs, concept_vecs = map_concepts(unique, concepts, chapters)
    return unique, item_vecs, concept_vecs
