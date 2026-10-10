"""Local-first retrieval: SQLite + FTS5 + static embeddings, no network per query.

The hosted path (Neo4j Aura + Gemini embeddings) is untouched; this package is
selected by `config.RETRIEVAL_BACKEND == "local"`.
"""
