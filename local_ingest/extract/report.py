"""Kept/dropped bookkeeping and per-document JSONL merging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def merge_jsonl(target: Path, rows: list[dict[str, Any]], *, replaced: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """Write `rows`, keeping earlier rows except those `replaced` says this run re-made."""
    previous = []
    if target.exists():
        previous = [r for r in map(json.loads, target.read_text().splitlines()) if not replaced(r)]
    merged = sorted(previous + rows, key=lambda r: r["id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged))
    return merged


class Report:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.dropped: list[dict[str, Any]] = []

    def drop(self, document: str, ref: Any, reason: str) -> None:
        self.dropped.append({"document": document, "ref": ref, "reason": reason})

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"documents": self.documents, "dropped": self.dropped}, indent=1, ensure_ascii=False))
