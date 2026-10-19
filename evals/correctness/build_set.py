"""Rebuild ``problems.yaml`` from official keys plus sympy-checked authored items.

Official stems are copied from the local exam extracts (``.local-run``). Authored
answers are written by ``score.recompute``, not by hand. Re-run from ``Aeropt-agent``:

    .venv/bin/python -m evals.correctness.build_set
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from evals.correctness.score import PROBLEMS_PATH, recompute

ROOT = Path(__file__).resolve().parents[2]
PHYSICS_PATH = ROOT / ".local-run" / "sources" / "exams" / "jee_advanced.jsonl"
MATHS_PATH = ROOT / ".local-run" / "jeechallenger" / "questions_mathematics.jsonl"
CHEM_PATH = ROOT / ".local-run" / "jeechallenger" / "questions_chemistry.jsonl"

# Self-contained official physics items: no figure, a single numeric key.
PHYSICS_IDS = [
    "jee_adv_2015_p2_phy_q03",
    "jee_adv_2015_p1_phy_q05",
    "jee_adv_2015_p2_phy_q02",
    "jee_adv_2016_p1_phy_q16",
    "jee_adv_2017_p1_phy_q08",
    "jee_adv_2017_p1_phy_q11",
    "jee_adv_2022_p2_phy_q01",
    "jee_adv_2022_p2_phy_q07",
    "jee_adv_2023_p1_phy_q11",
    "jee_adv_2023_p1_phy_q12",
    "jee_adv_2024_p2_phy_q09",
    "jee_adv_2025_p1_phy_q08",
]

# (paper id, question number) among OFFICIAL_KEY numeric items with no diagram.
MATHS_KEYS = [
    ("JEE_Main_2026_Apr_8th_Shift_2", 22),
    ("JEE_Main_2026_Apr_6th_Shift_1", 25),
    ("JEE_Main_2026_Apr_2nd_Shift_1", 22),
    ("JEE_Main_2026_Apr_2nd_Shift_2", 24),
    ("JEE_Main_2026_Apr_2nd_Shift_1", 25),
    ("JEE_Main_2026_Apr_5th_Shift_2", 25),
]
CHEM_KEYS = [
    ("JEE_Main_2026_Apr_5th_Shift_2", 71),
    ("JEE_Main_2026_Apr_6th_Shift_2", 73),
    ("JEE_Main_2026_Apr_6th_Shift_2", 75),
    ("JEE_Main_2026_Apr_8th_Shift_2", 75),
]


def _authored() -> list[dict]:
    rows = [
        {
            "id": "cosh_integral_n1",
            "subject": "mathematics",
            "topic": "definite_integral",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "The function $f$ is positive and twice differentiable and satisfies\n\n"
                "$$f f'' - (f')^2 = f^2 \\ln f,$$\n\n"
                "with $f(0) = e$ and $f'(0) = 0$. Find $f(x)$. Then evaluate\n\n"
                "$$I = \\int_0^{\\pi} \\cosh x \\cos x \\, dx$$\n\n"
                "and give the numerical value of $I$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.02, "rel_tol": 0.01},
            "verify": {"op": "integrate", "expr": "cosh(x)*cos(x)", "var": "x", "lo": "0", "hi": "pi"},
            "notes": "The cos^n identity gives pi/2 here. The integral is -sinh(pi)/2.",
        },
        {
            "id": "cosh_integral_n2",
            "subject": "mathematics",
            "topic": "definite_integral",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Evaluate\n\n"
                "$$I_2 = \\int_0^{\\pi} \\cosh^2 x \\cos(2x) \\, dx$$\n\n"
                "and give the numerical value of $I_2$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.05, "rel_tol": 0.01},
            "verify": {"op": "integrate", "expr": "cosh(x)**2 * cos(2*x)", "var": "x", "lo": "0", "hi": "pi"},
            "notes": "The cos identity would give pi/4. Direct integration does not.",
        },
        {
            "id": "cos_integral_n2",
            "subject": "mathematics",
            "topic": "definite_integral",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Evaluate\n\n"
                "$$J = \\int_0^{\\pi} \\cos^2 x \\cos(2x) \\, dx$$\n\n"
                "and give the numerical value of $J$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.01, "rel_tol": 0.01},
            "verify": {"op": "integrate", "expr": "cos(x)**2 * cos(2*x)", "var": "x", "lo": "0", "hi": "pi"},
            "notes": "Control. For cos, the identity integral_0^pi cos^n(x) cos(nx) dx = pi/2^n is correct, so J = pi/4.",
        },
        {
            "id": "ode_y_at_1",
            "subject": "mathematics",
            "topic": "differential_equation",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Solve $\\dfrac{dy}{dx} = y + x$ with $y(0) = 1$. "
                "Give the numerical value of $y(1)$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.02, "rel_tol": 0.005},
            "verify": {"op": "evalf", "expr": "-2 + 2*E"},
            "notes": "Closed form y = -x + 2*exp(x) - 1, so y(1) = -2 + 2*e.",
        },
        {
            "id": "f_at_1",
            "subject": "mathematics",
            "topic": "differential_equation",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "The function $f$ is positive and twice differentiable and satisfies\n\n"
                "$$f f'' - (f')^2 = f^2 \\ln f,$$\n\n"
                "with $f(0) = e$ and $f'(0) = 0$. Give the numerical value of $f(1)$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.03, "rel_tol": 0.005},
            "verify": {"op": "evalf", "expr": "exp(cosh(1))"},
            "notes": "f(x) = exp(cosh x) satisfies the IVP. This scores f, not the integral.",
        },
        {
            "id": "series_k_over_2k",
            "subject": "mathematics",
            "topic": "series",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Evaluate the infinite sum\n\n"
                "$$S = \\sum_{k=1}^{\\infty} \\frac{k}{2^k}.$$\n\n"
                "Give the value of $S$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.01, "rel_tol": 0.0},
            "verify": {"op": "summation", "expr": "k/2**k", "var": "k", "lo": "1", "hi": "oo"},
        },
        {
            "id": "binomial_sum_n5",
            "subject": "mathematics",
            "topic": "series",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Evaluate\n\n"
                "$$S = \\sum_{k=0}^{5} k \\binom{5}{k}.$$\n\n"
                "Give the value of $S$."
            ),
            "expect": {"kind": "integer", "abs_tol": 0.05, "rel_tol": 0.0},
            "verify": {"op": "evalf", "expr": "5 * 2**(4)"},
            "notes": "sum k*C(n,k) = n*2^(n-1).",
        },
        {
            "id": "limit_tan_minus_x",
            "subject": "mathematics",
            "topic": "limit",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Evaluate\n\n"
                "$$L = \\lim_{x \\to 0} \\frac{\\tan x - x}{x^3}.$$\n\n"
                "Give the value of $L$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.015, "rel_tol": 0.0},
            "verify": {"op": "limit", "expr": "(tan(x) - x)/x**3", "var": "x", "point": "0"},
            "notes": "The limit is 1/3. A common misremember is 1/2.",
        },
        {
            "id": "cubic_root_in_unit_interval",
            "subject": "mathematics",
            "topic": "root",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "The equation $x^3 - 3x + 1 = 0$ has a unique real root in the open interval $(0, 1)$. "
                "Find that root."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.0015, "rel_tol": 0.0},
            "verify": {"op": "nsolve", "expr": "x**3 - 3*x + 1", "var": "x", "guess": 0.3},
        },
        {
            "id": "integral_ln_over_x",
            "subject": "mathematics",
            "topic": "definite_integral",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Evaluate\n\n"
                "$$I = \\int_0^1 \\frac{\\ln(1+x)}{x} \\, dx.$$\n\n"
                "Give the numerical value of $I$."
            ),
            "expect": {"kind": "numeric", "abs_tol": 0.02, "rel_tol": 0.01},
            "verify": {"op": "integrate", "expr": "log(1+x)/x", "var": "x", "lo": "0", "hi": "1"},
            "notes": "Equals pi**2/12. pi**2/6 is the trap.",
        },
        {
            "id": "conditional_two_heads",
            "subject": "mathematics",
            "topic": "probability",
            "source": "authored",
            "source_id": "",
            "prompt": (
                "Three fair coins are tossed. Given that at least one coin shows heads, "
                "let $p$ be the probability of exactly two heads. Find $7p$."
            ),
            "expect": {"kind": "integer", "abs_tol": 0.05, "rel_tol": 0.0},
            "verify": {"op": "evalf", "expr": "7 * binomial(3, 2) / (2**3 - 1)"},
            "notes": "P(exactly 2 | at least 1) = 3/7, so 7p = 3.",
        },
    ]
    for row in rows:
        value = recompute(row["verify"])
        if row["expect"]["kind"] == "integer":
            value = float(round(value))
        row["expect"]["value"] = value
    return rows


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _physics() -> list[dict]:
    by_id = {row["id"]: row for row in _load_jsonl(PHYSICS_PATH)}
    out = []
    for item_id in PHYSICS_IDS:
        row = by_id[item_id]
        answer = str(row["answer"]).strip()
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", answer):
            raise SystemExit(f"{item_id} answer is not a single number: {answer!r}")
        value = float(answer)
        kind = "integer" if row["qtype"] == "integer" or value.is_integer() else "numeric"
        out.append(
            {
                "id": item_id,
                "subject": "physics",
                "topic": row["qtype"],
                "source": "jee_advanced_official",
                "source_id": item_id,
                "prompt": row["stem"].strip(),
                "expect": {
                    "kind": kind,
                    "value": value,
                    "abs_tol": 0.05,
                    "rel_tol": 0.0,
                },
                "notes": f"JEE Advanced {row['year']} Paper {row['paper']} Q{row['qno']}. Official key: {row['answer_raw']}.",
            }
        )
    return out


def _slug(paper: str, qno: int, subject: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", paper.lower()).strip("_")
    return f"{subject}_{slug}_q{qno}"


def _bank(path: Path, keys: list[tuple[str, int]], subject: str, source: str) -> list[dict]:
    rows = _load_jsonl(path)
    out = []
    for paper, qno in keys:
        matches = [
            row for row in rows
            if row.get("original_paper_id") == paper and int(row.get("question_number") or 0) == qno
        ]
        if len(matches) != 1:
            raise SystemExit(f"{paper} Q{qno}: found {len(matches)} rows")
        row = matches[0]
        if row.get("source_of_answer") != "OFFICIAL_KEY":
            raise SystemExit(f"{paper} Q{qno} is not an official key")
        if row.get("question_diagram_urls") or row.get("linked_passage_text"):
            raise SystemExit(f"{paper} Q{qno} depends on a figure or a passage")
        number = row.get("numeric_answer") or {}
        if number.get("exact_value") is None:
            raise SystemExit(f"{paper} Q{qno} has no exact value")
        value = float(number["exact_value"])
        out.append(
            {
                "id": _slug(paper, qno, subject),
                "subject": subject,
                "topic": str(row.get("chapter") or ""),
                "source": source,
                "source_id": f"{paper}#Q{qno}",
                "prompt": str(row.get("question_text") or "").strip(),
                "expect": {
                    "kind": "integer" if value.is_integer() else "numeric",
                    "value": value,
                    "abs_tol": 0.05,
                    "rel_tol": 0.0,
                },
                "notes": (
                    f"{paper} Q{qno}, {row.get('chapter')}, official key {value:g}, "
                    f"difficulty {row.get('difficulty')}."
                ),
            }
        )
    return out


def build() -> dict:
    problems = (
        _authored()
        + _physics()
        + _bank(MATHS_PATH, MATHS_KEYS, "mathematics", "jee_main_official")
        + _bank(CHEM_PATH, CHEM_KEYS, "chemistry", "jee_main_official")
    )
    ids = [row["id"] for row in problems]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate problem ids")
    if not 25 <= len(problems) <= 40:
        raise SystemExit(f"expected 25-40 problems, got {len(problems)}")
    return {
        "version": 1,
        "description": (
            "Hard problems with machine-checkable answers. Authored values were "
            "computed by sympy (see verify). Official values are copied from the "
            "exam key, not from a model."
        ),
        "problems": problems,
    }


def main() -> None:
    payload = build()
    PROBLEMS_PATH.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    print(f"wrote {len(payload['problems'])} problems to {PROBLEMS_PATH}")


if __name__ == "__main__":
    main()
