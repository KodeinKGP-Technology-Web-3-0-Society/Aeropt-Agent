"""push_index: the safety rails (hosted refusal, change detection) — no database needed."""

import numpy as np
import pytest

from local_ingest import push_index as pi


def test_hosted_url_refused_without_flag():
    with pytest.raises(SystemExit, match="not localhost"):
        pi.connect("postgresql://u:p@db.abc.supabase.co:5432/postgres", allow_hosted=False)


def test_vec_literal_refuses_another_width():
    from retrieval import embedder

    lit = pi.vec_literal(np.full(pi.DIM, 0.5, dtype=np.float32))
    assert lit.startswith("[") and lit.endswith("]") and lit.count(",") == pi.DIM - 1
    with pytest.raises(embedder.EmbeddingMismatch):
        pi.vec_literal(np.zeros(3072, dtype=np.float32))


def test_row_hash_changes_with_text_vector_or_model():
    v = np.ones(4, dtype=np.float32)
    base = pi.row_hash({"a": 1}, v, "m")
    assert base == pi.row_hash({"a": 1}, v.copy(), "m")
    assert base != pi.row_hash({"a": 2}, v, "m")
    assert base != pi.row_hash({"a": 1}, v * 2, "m")
    assert base != pi.row_hash({"a": 1}, v, "other-model")


def test_content_tuple_matches_template_arity():
    it = {k: "" for k in ("content_id", "content_type", "subject", "chapter", "chapter_id", "unit",
                          "source_book", "source_ref", "license", "heading", "core_text", "solution",
                          "concept_id", "question_type", "target_exam", "fingerprint")}
    it.update(secondary_concepts=["x"], difficulty=1, in_syllabus=True, has_solution=False,
              is_question=False, token_len=3, structured=None)
    t = pi.content_tuple(it, np.zeros(pi.DIM, dtype=np.float32), "m")
    placeholders = pi.CONTENT_TEMPLATE.count("%s")
    assert len(t) == placeholders
