"""Per-page text layer, page images, and whether a page's text can be trusted."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def page_texts(pdf: Path) -> list[str]:
    """Text layer of each page, in order ('' for a scanned page)."""
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True)
    pages = out.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]  # the text layer ends with a form feed
    return pages


def render(pdf: Path, page: int, out_dir: Path, dpi: int = 150) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"page-{page:02d}.png"
    if not target.exists():
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", "-singlefile", "-f", str(page), "-l", str(page),
             str(pdf), str(target.with_suffix(""))],
            check=True,
        )
    return target


# Glyphs a symbol font leaves behind when its mapping is lost: NCERT prints ρ as "r",
# Ω as "W" and ∝ as "µ", which no score can detect, but the same fonts also leave these.
_GARBLE = re.compile(r"[-�]|[µ¶§¬]|\b[a-zA-Z]\s*=\s*$", re.M)
_EQUATION_LINE = re.compile(r"^\s{20,}\S.{0,40}\(\d+\.\d+\)\s*$", re.M)  # "   V = R I      (3.3)"


def text_is_trustworthy(text: str) -> bool:
    """True when a page's text layer can stand in for reading the page image.

    Conservative: any sign of symbol-font loss or a numbered display equation sends
    the page to the vision reader. Prose-only pages pass.
    """
    if len(text.strip()) < 200:
        return False
    return not (_GARBLE.search(text) or _EQUATION_LINE.search(text))
