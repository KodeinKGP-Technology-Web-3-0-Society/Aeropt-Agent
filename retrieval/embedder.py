"""Embeddings for retrieval — local and fast by default.

Why not the hosted setup: one Gemini `embed_content` call measured ~1175 ms, and
it sat on the critical path of every turn (config.RETRIEVAL_TIMEOUT_MS documents
exactly that). A static model2vec model embeds a query in well under a
millisecond on CPU with no network at all, which is what makes the local pipeline
answer in tens of milliseconds instead of seconds.

Static embeddings are weaker than a transformer per-vector, so search.py never
relies on them alone: BM25 over FTS5 and the concept graph carry the precision,
dense similarity carries the recall, and rank fusion combines them.

`AEROPT_EMBED_BACKEND=gemini` switches to the hosted embedding model when a
build wants matched vectors with the Aura index. Vectors from different backends
are never mixed: the model name and dimension are recorded in the index and
checked at load.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

STATIC_MODEL = os.getenv("AEROPT_STATIC_EMBED_MODEL", "minishlab/potion-base-8M")
BACKEND = os.getenv("AEROPT_EMBED_BACKEND", "static").strip().lower()

# The index build snapshots the static model here so a query never needs the
# network — the whole point of the local backend.
LOCAL_MODEL_DIR = Path(__file__).resolve().parents[1] / ".local-run" / "models" / STATIC_MODEL.split("/")[-1]

_lock = threading.Lock()
_model = None
_gemini = None


def _static():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from model2vec import StaticModel

                source = str(LOCAL_MODEL_DIR) if (LOCAL_MODEL_DIR / "model.safetensors").exists() else STATIC_MODEL
                _model = StaticModel.from_pretrained(source)
    return _model


def model_name() -> str:
    return STATIC_MODEL if BACKEND == "static" else os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")


def dimension() -> int:
    return int(encode(["dimension probe"]).shape[1])


def _gemini_encode(texts: Sequence[str]) -> np.ndarray:
    global _gemini
    if _gemini is None:
        from google import genai

        import config

        _gemini = genai.Client(api_key=config.GEMINI_API_KEY)
    model = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
    rows = []
    for text in texts:
        result = _gemini.models.embed_content(model=model, contents=text[:8000] or "physics")
        rows.append(list(result.embeddings[0].values))
    return np.asarray(rows, dtype=np.float32)


def encode(texts: Iterable[str], *, normalize: bool = True) -> np.ndarray:
    """Embed texts into a float32 matrix, L2-normalised so dot product == cosine."""
    items = [t if isinstance(t, str) and t.strip() else "physics" for t in texts]
    if not items:
        return np.zeros((0, 1), dtype=np.float32)
    if BACKEND == "gemini":
        vecs = _gemini_encode(items)
    else:
        vecs = np.asarray(_static().encode(items), dtype=np.float32)
    if normalize:
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms
    return vecs.astype(np.float32, copy=False)


def encode_one(text: str) -> np.ndarray:
    return encode([text])[0]


def snapshot_static_model() -> Path:
    """Copy the static model into .local-run so runtime is fully offline."""
    LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if (LOCAL_MODEL_DIR / "model.safetensors").exists():
        return LOCAL_MODEL_DIR
    from model2vec import StaticModel

    StaticModel.from_pretrained(STATIC_MODEL).save_pretrained(str(LOCAL_MODEL_DIR))
    return LOCAL_MODEL_DIR
