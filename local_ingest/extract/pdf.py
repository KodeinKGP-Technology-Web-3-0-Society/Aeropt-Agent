"""Per-page text layer and page images."""

from __future__ import annotations

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
