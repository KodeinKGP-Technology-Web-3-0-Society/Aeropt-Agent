"""Episodic-memory setup check. Booleans only: no URLs, keys, or passwords.

    python -m agents.memory_status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Sequence

import config
from db.supabase_client import db


def collect_status() -> Dict[str, Any]:
    """Probe the user database and whether the retrieval URL is configured.

    A missing table is reported as false. This does not apply migrations and
    does not treat a failed probe as proof that a migration ran.
    """
    notes = []
    table = _probe(lambda: db.episodes_enabled())
    if table is False:
        notes.append("public.episodic_memory is not queryable. Apply db/migrations/017_episodic_memory.sql on the user project.")
    approach = False if table is False else _probe(lambda: db.episode_approach_enabled())
    consolidated = False if table is False else _probe(
        lambda: db._column_enabled("_consolidated_at_enabled", "episodic_memory", "consolidated_at")
    )
    consolidation_table = _probe(
        lambda: db._column_enabled("_consolidation_table_enabled", "episodic_consolidation", "cluster_key")
    )
    learner = _probe(lambda: db.memory_enabled())
    if approach is False:
        notes.append("episodic_memory.approach is missing. Apply db/migrations/018_episodic_memory_approach.sql after 017.")
    if consolidated is False or consolidation_table is False:
        notes.append("Consolidation storage is missing. Apply db/migrations/018_episodic_memory_approach.sql on the user project.")
    if learner is False:
        notes.append("public.learner_memory is not queryable. Apply db/migrations/006_learner_memory.sql.")

    retrieval_set = bool(
        (os.getenv("RETRIEVAL_DATABASE_URL") or getattr(config, "RETRIEVAL_DATABASE_URL", "") or "").strip()
    )
    if not retrieval_set:
        notes.append(
            "RETRIEVAL_DATABASE_URL is unset. Concept lookup needs the retrieval project's Postgres URI, not the user DATABASE_URL."
        )

    checks = {
        "episodic_memory_table": table,
        "approach_column": approach,
        "consolidated_at_column": consolidated,
        "consolidation_table": consolidation_table,
        "learner_memory_table": learner,
        "retrieval_database_url_configured": retrieval_set,
    }
    return {
        "ok": all(value is True for value in checks.values()),
        "checks": checks,
        "notes": notes,
    }


def log_episodic_status(log: Any) -> None:
    """Startup line. Fail open: a probe error must not stop the server."""
    try:
        status = collect_status()
    except Exception:
        log.warning("episodic memory status check failed")
        return
    if status["ok"]:
        log.warning("episodic memory setup: ok")
        return
    missing = [name for name, value in status["checks"].items() if value is not True]
    log.warning("episodic memory setup incomplete: %s", ", ".join(missing))


def _probe(fn) -> Optional[bool]:
    try:
        return bool(fn())
    except Exception:
        return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(description="Show episodic-memory setup without secrets.").parse_args(
        list(argv) if argv is not None else None
    )
    print(json.dumps(collect_status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
