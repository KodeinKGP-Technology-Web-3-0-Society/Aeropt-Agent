"""Load agent skill YAML definitions aligned with config.py model assignments."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@lru_cache(maxsize=32)
def _load_skill_cached(path_str: str, mtime: float) -> Optional[Dict[str, Any]]:
    with open(path_str, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_skill(name: str) -> Optional[Dict[str, Any]]:
    clean_name = name
    if clean_name.startswith("skills/"):
        clean_name = clean_name[len("skills/"):]
    if clean_name.endswith(".yaml"):
        clean_name = clean_name[:-5]

    path = _SKILLS_DIR / f"{clean_name}.yaml"
    if not path.is_file():
        return None
    return _load_skill_cached(str(path), path.stat().st_mtime)


def get_skill_version(name: str) -> str:
    """Return sha1 hash of the skill YAML file bytes (first 12 characters)."""
    import hashlib

    clean_name = name
    if clean_name.startswith("skills/"):
        clean_name = clean_name[len("skills/"):]
    if clean_name.endswith(".yaml"):
        clean_name = clean_name[:-5]

    path = _SKILLS_DIR / f"{clean_name}.yaml"
    if not path.is_file():
        return ""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


