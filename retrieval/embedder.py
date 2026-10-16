"""The one embedding model for retrieval. There is no second one and no switch.

    MODEL_ID = "minishlab/potion-base-8M"   static model2vec, 256-d, L2-normalised

Every vector in the retrieval database (`content.embedding`, `concepts.embedding`) and
every query vector comes from this function, so the two always live in the same space:

  * ingest  — `local_ingest/push_index.py` embeds with `encode()` and writes `embed_model`
              beside each vector; the migration's CHECK constraint refuses any other model.
  * query   — the retrieval pipeline embeds the student's text with `encode_one()`.
  * startup — `retrieval.store.verify_embedding_contract()` compares the database's
              recorded model and dimension with these constants and refuses to serve
              on a mismatch (ranking noise is worse than an error).

Changing the model is a deliberate migration, never an env var: new MODEL_ID/DIM here,
a new `vector(N)` + CHECK in SQL, then a full `make push` (every row re-embeds because
the model is part of each row's hash).

Why a static model: a query embeds in well under a millisecond on CPU with no network
(one Gemini embed call measured ~1175 ms). Static vectors are weaker per-vector, so the
pipeline never relies on them alone — full-text ranking and the concept graph carry the
precision, dense similarity the recall, and rank fusion combines them.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable

import numpy as np

MODEL_ID = "minishlab/potion-base-8M"
DIM = 256

# Snapshotted here so a query never needs the network (and the Docker image can run offline).
LOCAL_MODEL_DIR = Path(__file__).resolve().parents[1] / ".local-run" / "models" / MODEL_ID.split("/")[-1]

_lock = threading.Lock()
_model = None


class EmbeddingMismatch(RuntimeError):
    """Stored vectors and query vectors would come from different models or widths."""


def _static():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from model2vec import StaticModel

                source = str(LOCAL_MODEL_DIR) if (LOCAL_MODEL_DIR / "model.safetensors").exists() else MODEL_ID
                _model = StaticModel.from_pretrained(source)
    return _model


def model_name() -> str:
    return MODEL_ID


def dimension() -> int:
    return DIM


def encode(texts: Iterable[str]) -> np.ndarray:
    """Embed texts into a float32 (n, DIM) matrix, L2-normalised so dot product == cosine."""
    items = [t if isinstance(t, str) and t.strip() else "physics" for t in texts]
    if not items:
        return np.zeros((0, DIM), dtype=np.float32)
    vecs = np.asarray(_static().encode(items), dtype=np.float32)
    if vecs.shape[1] != DIM:
        raise EmbeddingMismatch(f"{MODEL_ID} produced {vecs.shape[1]}-d vectors, expected {DIM}")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vecs / norms).astype(np.float32, copy=False)


def encode_one(text: str) -> np.ndarray:
    return encode([text])[0]


def to_pg(vec: np.ndarray) -> str:
    """pgvector text literal. Only vectors from this module should ever be written or queried."""
    v = np.asarray(vec, dtype=np.float32)
    if v.shape != (DIM,):
        raise EmbeddingMismatch(f"vector of shape {v.shape}, expected ({DIM},) from {MODEL_ID}")
    return "[" + ",".join(f"{x:.7g}" for x in v.tolist()) + "]"


def snapshot_static_model() -> Path:
    """Copy the model into .local-run so runtime is fully offline."""
    LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if (LOCAL_MODEL_DIR / "model.safetensors").exists():
        return LOCAL_MODEL_DIR
    from model2vec import StaticModel

    StaticModel.from_pretrained(MODEL_ID).save_pretrained(str(LOCAL_MODEL_DIR))
    return LOCAL_MODEL_DIR
