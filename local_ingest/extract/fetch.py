"""Download a source document once, verify it is a PDF, record its hash."""

from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path


def fetch(url: str, target: Path, *, attempts: int = 4) -> str:
    """Download `url` to `target` unless it is already there. Returns the sha256."""
    if not target.exists() or target.stat().st_size < 1000:
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(attempts):
            subprocess.run(["curl", "-s", "-L", "-m", "180", "-o", str(target), url], check=False)
            # The PDF header may follow stray bytes (2017's papers open with "\r\n"), which
            # readers accept; anything without one in the first KB is an error page.
            if target.exists() and target.stat().st_size > 1000 and b"%PDF" in target.read_bytes()[:1024]:
                break
            time.sleep(10 * (attempt + 1))  # the Internet Archive rate-limits bursts
        else:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"could not download a PDF from {url}")
    return hashlib.sha256(target.read_bytes()).hexdigest()
