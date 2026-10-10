"""Download the OpenStax University Physics CNXML bundle into .local-run/sources.

Why this source: ncert.nic.in is not reachable from this machine, and the point of
this pipeline is that nobody hand-extracts syllabus content. OpenStax ships the
whole of calculus-based physics as structured CNXML — chapters, sections, worked
examples *with* solutions, end-of-section exercises and per-module glossary
definitions — which is everything the corpus builder needs to derive a syllabus
without a human writing concept tables.

License: CC BY-NC-SA 4.0 (recorded per item as `license` so the source can be
swapped later without re-deriving anything else).

    .venv/bin/python -m local_ingest.fetch_openstax
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".local-run" / "sources" / "openstax"

REPO = "openstax/osbooks-university-physics-bundle"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
TREE = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"

LICENSE = "CC BY-NC-SA 4.0"
SOURCE_BOOK = "OpenStax University Physics"

COLLECTIONS = [
    "university-physics-volume-1",
    "university-physics-volume-2",
    "university-physics-volume-3",
]

_UA = {"User-Agent": "aeropt-local-ingest"}


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_to(url: str, path: Path) -> tuple[Path, int]:
    if path.exists() and path.stat().st_size > 0:
        return path, 0
    for attempt in range(4):
        try:
            data = _get(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path, len(data)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            if attempt == 3:
                raise SystemExit(f"Failed to download {url}: {exc}")
    return path, 0


def module_ids() -> list[str]:
    """Every module id in the bundle, from the git tree (one API call)."""
    tree_path = DEST / "tree.json"
    if not tree_path.exists():
        _fetch_to(TREE, tree_path)
    tree = json.loads(tree_path.read_text())
    if tree.get("truncated"):
        raise SystemExit("GitHub tree response was truncated; cannot enumerate modules.")
    ids = sorted(
        entry["path"].split("/")[1]
        for entry in tree["tree"]
        if entry["path"].startswith("modules/") and entry["path"].endswith("index.cnxml")
    )
    if not ids:
        raise SystemExit("No CNXML modules found in the OpenStax tree.")
    return ids


def fetch_all(workers: int = 12) -> dict[str, Path]:
    DEST.mkdir(parents=True, exist_ok=True)
    for slug in COLLECTIONS:
        _fetch_to(f"{RAW}/collections/{slug}.collection.xml", DEST / "collections" / f"{slug}.xml")

    ids = module_ids()
    targets = [(f"{RAW}/modules/{mid}/index.cnxml", DEST / "modules" / f"{mid}.cnxml") for mid in ids]
    missing = [t for t in targets if not t[1].exists()]
    print(f"OpenStax: {len(ids)} modules, {len(missing)} to download")

    downloaded = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (_, size) in enumerate(pool.map(lambda t: _fetch_to(*t), missing), start=1):
            downloaded += size
            if i % 50 == 0:
                print(f"  {i}/{len(missing)}")
    print(f"OpenStax: {downloaded / 1_048_576:.1f} MB downloaded into {DEST}")
    return {mid: DEST / "modules" / f"{mid}.cnxml" for mid in ids}


def main() -> None:
    paths = fetch_all()
    total = sum(p.stat().st_size for p in paths.values())
    print(f"{len(paths)} modules on disk, {total / 1_048_576:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
