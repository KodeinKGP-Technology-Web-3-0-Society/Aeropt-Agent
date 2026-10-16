"""One embedding model, named in exactly the places that must agree.

retrieval/embedder.py is the source of truth. The migration hard-codes the model in its
CHECK constraints and the width in vector(N); if either drifts from the code, the database
would accept (or the agent would query with) vectors from another space.
"""

import re
from pathlib import Path

import numpy as np

from retrieval import embedder

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "db" / "migrations" / "017_retrieval_pgvector.sql").read_text()


def test_migration_checks_the_embedder_model():
    models = set(re.findall(r"embed_model = '([^']+)'", MIGRATION))
    assert models == {embedder.MODEL_ID}


def test_migration_width_is_the_embedder_dimension():
    widths = set(re.findall(r"vector\((\d+)\)", MIGRATION))
    assert widths == {str(embedder.DIM)}


def test_encode_is_normalised_and_the_declared_width():
    vecs = embedder.encode(["torque on a rigid body", "aufbau principle"])
    assert vecs.shape == (2, embedder.DIM)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)


def test_no_second_embedding_model_in_the_runtime():
    """Nothing outside the embedder may call a hosted embedding API or name another model."""
    offenders = []
    for path in ROOT.glob("**/*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "scratch", "tests/")) or "__pycache__" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "embed_content(" in text or "gemini-embedding" in text or "AEROPT_EMBED_BACKEND" in text:
            offenders.append(rel)
    assert offenders == []
