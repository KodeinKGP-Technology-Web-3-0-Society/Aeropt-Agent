"""MathML → LaTeX, good enough for retrieval and for an LLM to read back.

CNXML carries every formula as Presentation MathML. Left as raw XML it destroys
both the embedding (hundreds of tag tokens per formula) and the tutor's ability
to quote a formula correctly. Converted to LaTeX it reads the way the rest of the
system already writes maths.

This is deliberately a presentation-level transliteration, not a semantic parse:
the aim is a faithful, readable string, not a re-derivable expression tree.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

# Unicode operators that have a conventional LaTeX spelling.
_OPS = {
    "×": r"\times", "·": r"\cdot", "−": "-", "–": "-", "—": "-",
    "≈": r"\approx", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq",
    "→": r"\to", "⇒": r"\Rightarrow", "±": r"\pm", "∓": r"\mp",
    "∞": r"\infty", "∫": r"\int", "∑": r"\sum", "∏": r"\prod",
    "∂": r"\partial", "∇": r"\nabla", "√": r"\sqrt", "∝": r"\propto",
    "°": r"^{\circ}", "′": "'", "″": "''", "≡": r"\equiv", "∈": r"\in",
    "⋅": r"\cdot", "∮": r"\oint", "⟨": r"\langle", "⟩": r"\rangle",
    # A bare brace from an <mo> fence is a delimiter in the source but a *grouping*
    # character in LaTeX, so it silently unbalanced every piecewise definition it
    # appeared in (54 spans in the corpus). Escaped, it renders and balances.
    "{": r"\{", "}": r"\}",
    "〈": r"\langle", "〉": r"\rangle", "ℏ": r"\hbar", "ℓ": r"\ell",
    "∈": r"\in", "∉": r"\notin", "∪": r"\cup", "∩": r"\cap",
    "⊥": r"\perp", "∥": r"\parallel", "∠": r"\angle", "△": r"\triangle",
    "≅": r"\cong", "∼": r"\sim", "≪": r"\ll", "≫": r"\gg",
    "⇌": r"\rightleftharpoons", "↔": r"\leftrightarrow", "⇔": r"\Leftrightarrow",
    "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists", "¬": r"\neg",
    "…": r"\ldots", "⋯": r"\cdots", "⃗": "",  # combining arrow: \vec handles it
}

# `\text{sin}` typesets upright but loses the operator spacing and the semantics.
# MathML writes function names as <mtext>, so they arrive that way in bulk.
_FUNCTIONS = frozenset(
    """sin cos tan cot sec csc sinh cosh tanh coth arcsin arccos arctan
    log ln lg exp lim max min sup inf det gcd deg arg Pr""".split()
)

_GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\phi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "ϕ": r"\phi",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi",
    "Ψ": r"\Psi", "Ω": r"\Omega",
}


def _tag(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def _sym(text: str) -> str:
    out = []
    for ch in text or "":
        out.append(_OPS.get(ch) or _GREEK.get(ch) or ch)
    return "".join(out)


def _kids(el: ET.Element) -> list[str]:
    return [_node(child) for child in el]


def _join(el: ET.Element) -> str:
    parts = []
    if el.text and el.text.strip():
        parts.append(_sym(el.text.strip()))
    for child in el:
        parts.append(_node(child))
        if child.tail and child.tail.strip():
            parts.append(_sym(child.tail.strip()))
    return " ".join(p for p in parts if p)


def _brace(s: str) -> str:
    """Always brace: `\\frac12` is legal LaTeX but `\\vecp` and `\\fracpm` are not."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    return "{" + s + "}"


def _node(el: ET.Element) -> str:
    tag = _tag(el)
    text = (el.text or "").strip()

    if tag in {"mi", "mn"}:
        return _sym(text)
    if tag == "mo":
        op = _sym(text)
        return op
    if tag == "mtext":
        cleaned = _sym(text)
        if not cleaned:
            return ""
        if cleaned in _FUNCTIONS:
            return "\\" + cleaned
        if re.fullmatch(r"[A-Za-z][A-Za-z/·\s%°.,]*", cleaned):
            return r"\text{" + cleaned + "}"
        return cleaned
    if tag == "mspace":
        return r"\,"
    if tag in {"mrow", "mstyle", "math", "mpadded", "semantics", "mphantom"}:
        return _join(el)
    if tag == "mfrac":
        kids = _kids(el)
        if len(kids) == 2:
            return r"\frac" + _brace(kids[0]) + _brace(kids[1])
        return _join(el)
    if tag == "msup":
        kids = _kids(el)
        if len(kids) == 2:
            return kids[0] + "^" + _brace(kids[1])
        return _join(el)
    if tag == "msub":
        kids = _kids(el)
        if len(kids) == 2:
            return kids[0] + "_" + _brace(kids[1])
        return _join(el)
    if tag == "msubsup":
        kids = _kids(el)
        if len(kids) == 3:
            return kids[0] + "_" + _brace(kids[1]) + "^" + _brace(kids[2])
        return _join(el)
    if tag == "msqrt":
        return r"\sqrt" + _brace(_join(el))
    if tag == "mroot":
        kids = _kids(el)
        if len(kids) == 2:
            return r"\sqrt[" + kids[1] + "]" + _brace(kids[0])
        return _join(el)
    if tag in {"mover", "munder", "munderover"}:
        kids = _kids(el)
        if tag == "mover" and len(kids) == 2:
            accent = kids[1].strip()
            if accent in {"→", r"\to", "⃗"}:
                return r"\vec" + _brace(kids[0])
            if accent == "^":
                return r"\hat" + _brace(kids[0])
            if accent in {"-", "―", "‾"}:
                return r"\bar" + _brace(kids[0])
            return r"\overset" + _brace(accent) + _brace(kids[0])
        if tag == "munder" and len(kids) == 2:
            return kids[0] + "_" + _brace(kids[1])
        if len(kids) == 3:
            return kids[0] + "_" + _brace(kids[1]) + "^" + _brace(kids[2])
        return _join(el)
    if tag in {"mfenced"}:
        return "(" + _join(el) + ")"
    if tag in {"mtable"}:
        rows = [" & ".join(_kids(r)) for r in el]
        body = r" \\ ".join(rows)
        return r"\begin{matrix}" + body + r"\end{matrix}"
    if tag in {"mtr", "mtd"}:
        return _join(el)
    if tag == "annotation":
        return ""
    return _join(el)


def mathml_to_latex(raw: str | ET.Element) -> str:
    """Convert one <math> element (string or parsed) into inline LaTeX."""
    try:
        el = raw if isinstance(raw, ET.Element) else ET.fromstring(raw)
    except ET.ParseError:
        return ""
    latex = _node(el)
    latex = re.sub(r"\s+", " ", latex).strip()
    latex = re.sub(r"\s*([_^])\s*", r"\1", latex)
    latex = latex.replace(r"\, ", r"\,")
    return latex


def inline(latex: str) -> str:
    return f"${latex}$" if latex else ""
