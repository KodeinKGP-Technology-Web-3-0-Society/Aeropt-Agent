"""Refuse any ingest/index rebuild that could touch the deployed graph or DB."""

from __future__ import annotations

_FORBIDDEN_NEO4J = ("neo4j+s://", "neo4j+ssc://", "databases.neo4j.io")
_FORBIDDEN_SUPABASE = ("supabase.co",)


def assert_local_neo4j(uri: str) -> None:
    lowered = (uri or "").strip().lower()
    if not lowered:
        raise SystemExit("NEO4J_URI is empty.")
    if any(token in lowered for token in _FORBIDDEN_NEO4J):
        raise SystemExit(
            f"Refusing to write Neo4j at {uri!r}. Local ingest may only use "
            "bolt://127.0.0.1:7687 (or another local bolt URI)."
        )
    if not (lowered.startswith("bolt://") or lowered.startswith("neo4j://")):
        raise SystemExit(f"Refusing Neo4j URI {uri!r}; expected a local bolt URI.")


def assert_local_supabase(url: str) -> None:
    lowered = (url or "").strip().lower()
    if any(token in lowered for token in _FORBIDDEN_SUPABASE):
        raise SystemExit(
            f"Refusing to write Supabase at {url!r}. Local ingest may only use "
            "http://127.0.0.1:54321."
        )
    if "127.0.0.1" not in lowered and "localhost" not in lowered:
        raise SystemExit(f"Refusing Supabase URL {url!r}; expected localhost.")
