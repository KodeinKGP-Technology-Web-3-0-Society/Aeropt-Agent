"""Load agent skill YAML definitions aligned with config.py model assignments."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@lru_cache(maxsize=16)
def load_skill(name: str) -> Optional[Dict[str, Any]]:
    clean_name = name
    if clean_name.startswith("skills/"):
        clean_name = clean_name[len("skills/"):]
    if clean_name.endswith(".yaml"):
        clean_name = clean_name[:-5]

    path = _SKILLS_DIR / f"{clean_name}.yaml"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}

