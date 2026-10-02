"""TikZ / circuitikz → SVG, so the tutor can draw real physics diagrams.

The tutor writes the drawing the way it would in Overleaf; this compiles it with
`tectonic` (a self-contained LaTeX engine that fetches only the packages it
needs) and converts the PDF to SVG with `pdftocairo`. Glyphs are emitted as
paths, so labels look identical in every browser and the result stays vector.

Output is cached by content hash under var/diagrams/svg/<hash>.svg and served by
GET /api/v1/diagrams/<hash>.svg. A reply carries only `![caption](diagram:<hash>)`,
so the conversation history the model re-reads stays small.

Safety: the model supplies only the picture, never the preamble. File and shell
primitives are refused before compiling, tectonic never enables shell-escape,
and each compile runs in a memory/CPU-capped systemd scope with a timeout, one
at a time, so a pathological drawing cannot take the machine down.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("jee_tutor.diagrams")

_ROOT = Path(__file__).resolve().parent.parent
# Not a hidden directory: tectonic ships as a snap, and snaps cannot read
# dot-directories under $HOME.
DIAGRAM_DIR = Path(os.environ.get("AEROPT_DIAGRAM_DIR", str(_ROOT / "var" / "diagrams")))
SVG_DIR = DIAGRAM_DIR / "svg"
WORK_DIR = DIAGRAM_DIR / "work"

COMPILE_TIMEOUT_S = 60      # first use downloads packages; cached compiles take ~2 s
MAX_SOURCE_CHARS = 8000

_HASH_RE = re.compile(r"^[0-9a-f]{20}$")
_compile_lock = threading.Lock()

_PREAMBLE = r"""\documentclass[border=6pt]{standalone}
\usepackage{amsmath,amssymb}
\usepackage[siunitx,american]{circuitikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usetikzlibrary{arrows.meta,calc,positioning,decorations.pathmorphing,decorations.markings,patterns,angles,quotes,shapes.geometric,intersections}
\tikzset{>=Stealth}
\begin{document}
"""
_POSTAMBLE = "\n\\end{document}\n"

# Anything that reads/writes files, runs commands, or rewires TeX itself. The
# drawing needs none of these.
_FORBIDDEN = re.compile(
    r"\\(?:input|include|openin|openout|read|readline|write|immediate|newwrite|newread|"
    r"catcode|csname|endcsname|def|edef|gdef|xdef|let|futurelet|expandafter|"
    r"usepackage|RequirePackage|documentclass|begin\{document\}|end\{document\}|"
    r"special|pdfprimitive|directlua|luaexec|ShellEscape|write18|jobname|"
    r"lstinputlisting|verbatiminput|includegraphics|pgfplotstableread|addplot\s*table|"
    r"loop|repeat)\b",
)
_ENVIRONMENTS = ("tikzpicture", "circuitikz")


class DiagramError(ValueError):
    """The source was refused or did not compile; the message says why."""


def diagram_path(diagram_id: str) -> Optional[Path]:
    if not _HASH_RE.match(diagram_id or ""):
        return None
    path = SVG_DIR / f"{diagram_id}.svg"
    return path if path.exists() else None


def available() -> bool:
    return bool(shutil.which("tectonic") and shutil.which("pdftocairo"))


def _validate(source: str) -> str:
    src = (source or "").strip()
    # Accept a fenced block the model may have wrapped it in.
    src = re.sub(r"^```(?:latex|tex|tikz)?\s*|\s*```$", "", src).strip()
    if not src:
        raise DiagramError("Empty diagram source.")
    if len(src) > MAX_SOURCE_CHARS:
        raise DiagramError(f"Diagram source is too long ({len(src)} chars; keep it under {MAX_SOURCE_CHARS}).")
    bad = _FORBIDDEN.search(src)
    if bad:
        raise DiagramError(
            f"`{bad.group(0)}` is not allowed. Send only the tikzpicture / circuitikz "
            "environment; the preamble and packages are provided."
        )
    if not any(f"\\begin{{{env}}}" in src for env in _ENVIRONMENTS):
        raise DiagramError("Wrap the drawing in \\begin{tikzpicture}…\\end{tikzpicture} (or circuitikz).")
    return src


def _tex_error(log: str) -> str:
    """The first LaTeX error and its line, which is what the model needs to fix it."""
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("!") or line.startswith("error:"):
            context = " ".join(l.strip() for l in lines[i:i + 3] if l.strip())
            return context[:400]
    return (log.strip().splitlines() or ["LaTeX failed without an error message."])[-1][:400]


def _themeable(svg: str) -> str:
    """Pure black → currentColor, so the diagram follows the text colour when inlined."""
    return (
        svg.replace('fill="rgb(0%, 0%, 0%)"', 'fill="currentColor"')
        .replace('stroke="rgb(0%, 0%, 0%)"', 'stroke="currentColor"')
    )


def render_tikz(source: str) -> Dict[str, Any]:
    """Compile a TikZ / circuitikz picture to SVG. Returns {id, path, cached}.

    Raises DiagramError with a message the model can act on.
    """
    src = _validate(source)
    digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:20]
    out = SVG_DIR / f"{digest}.svg"
    if out.exists():
        return {"id": digest, "path": str(out), "cached": True}
    if not available():
        raise DiagramError("Diagram rendering is not installed on this server (needs tectonic and pdftocairo).")

    SVG_DIR.mkdir(parents=True, exist_ok=True)
    work = WORK_DIR / digest
    work.mkdir(parents=True, exist_ok=True)
    (work / "d.tex").write_text(_PREAMBLE + src + _POSTAMBLE, encoding="utf-8")

    cmd = ["tectonic", "-X", "compile", "--outdir", str(work), str(work / "d.tex")]
    if shutil.which("systemd-run"):
        # Own cgroup: a runaway compile is killed at the cap instead of the box.
        cmd = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=700M", "-p", "CPUQuota=100%", *cmd]

    with _compile_lock:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S, cwd=work)
        except subprocess.TimeoutExpired:
            shutil.rmtree(work, ignore_errors=True)
            raise DiagramError("The diagram took too long to compile; simplify it (fewer plot samples or loops).")
        pdf = work / "d.pdf"
        if proc.returncode != 0 or not pdf.exists():
            log = (proc.stdout or "") + "\n" + (proc.stderr or "")
            shutil.rmtree(work, ignore_errors=True)
            raise DiagramError(f"LaTeX error: {_tex_error(log)}")
        svg_tmp = work / "d.svg"
        conv = subprocess.run(
            ["pdftocairo", "-svg", str(pdf), str(svg_tmp)],
            capture_output=True, text=True, timeout=30,
        )
        if conv.returncode != 0 or not svg_tmp.exists():
            shutil.rmtree(work, ignore_errors=True)
            raise DiagramError("Could not convert the compiled diagram to SVG.")
        out.write_text(_themeable(svg_tmp.read_text(encoding="utf-8")), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)

    logger.info("diagram %s compiled (%d bytes)", digest, out.stat().st_size)
    return {"id": digest, "path": str(out), "cached": False}
