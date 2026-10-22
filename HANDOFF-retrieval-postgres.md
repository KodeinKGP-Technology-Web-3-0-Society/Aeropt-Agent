# Handoff: retrieval on Supabase Postgres (pgvector), no SQLite

Goal (from the owner): one embedding model, made explicit. Retrieval reads only the deployed
Supabase **retrieval** project (not the user-data project). No local SQLite index any more.
Chemistry (NCERT) is in the corpus beside physics.

## Already done (in the working tree, NOT committed)

- `retrieval/embedder.py`: the only embedding model. `MODEL_ID = "minishlab/potion-base-8M"`, `DIM = 256`,
  no env switch, `to_pg()` refuses other widths, `EmbeddingMismatch`.
- `db/migrations/019_retrieval_pgvector.sql`: idempotent. `vector(256)` columns + HNSW, a CHECK constraint
  on `embed_model`, `retrieval_meta`, `fts` tsvector, `chunk_concepts`, `chapters.subject`, and
  `syllabus_topics.subject`. It also reconciles hosted drift: int flags become booleans, `vec_row` is dropped,
  the `concept_name` UNIQUE constraint is dropped, old `match_*` RPCs are dropped, and `hnsw.ef_search=100` is set.
- `local_ingest/push_index.py` (`make push` / `make push-dry`): sources → syllabus → items → embeddings →
  upsert into `RETRIEVAL_DATABASE_URL`. It is hash-based (an unchanged re-push writes 0 rows), refuses
  non-localhost URLs without `--allow-hosted`, writes `retrieval_meta`, and validates the CHECK constraints.
- Chemistry concepts are derived from NCERT's numbered headings (`local_ingest/ncert_syllabus.py`: 19
  chapters, 405 concepts). `corpus.py` maps chemistry only within its chapter and keeps physics sources on
  physics chapters. Result: 0 cross-subject links.
- `retrieval/store.py` was rewritten on Postgres (psycopg2 pool), keeping its old function API.
  - Concepts, chapters and topics are cached in-process; the cache reloads when `retrieval_meta.built_at` changes.
  - The resolver rebuilds its lexicon via `store.cache_generation()`. That design fixed a deadlock.
  - The novelty ledger lives in the user DB `served_items` (`services/durable_state.load_served/save_served`).
    In tests it stays in memory (`AEROPT_LEDGER=memory`, `tests/conftest.py`).
- `retrieval/search.py`, `retrieval/curriculum_graph.py`, `mcp_tools/study_tools.py`, `services/study_overview.py`
  and the ingest tools now query Postgres. Dense scoring uses pgvector, lexical scoring uses `ts_rank_cd`.
- One retrieval path:
  - `RETRIEVAL_BACKEND` and the Neo4j config are removed from `config.py`.
  - The curriculum tools use `retrieval/bridge.py`; concept matching uses `retrieval/resolver.py`.
  - `api_server` verifies the embedding contract at startup.
- Deleted (still recoverable from git):
  - the old storage and retrieval paths: `retrieval/supabase_retriever.py`, `local_ingest/{build_index,syllabus_topics,run,guard}.py`,
    `create_vector_index.py`, `db/neo4j_client.py`;
  - the old deploy uploaders: `deploy/{public_index,upload_to_supabase}.py`;
  - legacy scripts with fake 768-d vectors: `scripts/{ingest_content,ingest_real_pyqs,test_local_retrieval}.py`.
- Dockerfile, deploy staging, `requirements.txt` (`psycopg2-binary` added, `neo4j` removed), `Makefile` and `ci/run.sh` are updated.
- New tests: `tests/test_embedding_contract.py`. Ported to Postgres: `tests/test_retrieval_postgres.py`,
  `tests/test_latex_contract.py`, `tests/test_durable_state.py`, `tests/test_push_index.py`.

Verified against the LOCAL Supabase Docker Postgres:
- push: 14,874 content rows (12,933 Physics, 1,941 Chemistry) and 1,517 concepts, all tagged with the model;
- old SQLite vs new Postgres on 20 queries: 19/20 resolved to the same concept, mean top-5 overlap 0.70, median 13 ms locally.

## Remaining, in order

1. **Run the tests.** The last run was interrupted after the final fixes.
   ```bash
   cd Aeropt-agent
   PW=$(docker inspect supabase-db --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-)
   export RETRIEVAL_DATABASE_URL="postgresql://postgres.your-tenant-id:${PW}@127.0.0.1:54322/postgres"
   .venv/bin/python -m pytest -q tests --ignore=tests/test_memory.py
   env -u RETRIEVAL_DATABASE_URL .venv/bin/python -m pytest -q tests --ignore=tests/test_memory.py   # CI: DB tests skip
   ```
   `tests/test_memory.py` and `memory/` are someone else's untracked work in progress. Their failure is
   theirs (`memory/__init__.py` shadows the `recall` module). Do not touch them.
2. **Hosted push.** The owner must first add `RETRIEVAL_DATABASE_URL` to `Aeropt-agent/.env`: the Postgres
   pooler URL of the retrieval project `stecalgmpaulkkwqlqcx` (Supabase dashboard → Connect). Then:
   `make push-dry ALLOW_HOSTED=1`, review it, then `make push ALLOW_HOSTED=1 PRUNE=1`.
   The owner decided the hosted DB serves **everything** (NCERT, H.C. Verma and the coaching papers included).
3. **Measure latency against hosted.** Run `.venv/bin/python -m retrieval.cli "explain torque"` a few times.
   Each turn makes about 3–6 SQL round trips. If it is too slow, merge the widening queries in `search.search`.
4. **Docs.**
   - `RETRIEVAL.md`'s last section ("Postgres + pgvector corpus") is outdated: it mentions `hybrid_match_content`,
     `supabase_retriever` and `SYLLABUS_SUBJECTS`. Rewrite it to match the above.
   - The root `../CLAUDE.md` retrieval section still describes SQLite, `make index` and `RETRIEVAL_BACKEND`.
     Update it, plus invariants 2, 5 and 9 (chemistry is now in the corpus).
   - Add `RETRIEVAL_DATABASE_URL` to the README's env table.
   - Replace `make index` in the docstrings under `local_ingest/` with `make push`.
     `EXTRACTION.md` has the owner's uncommitted edits, so edit only that one phrase there.
5. **Deploy env.** Set `RETRIEVAL_DATABASE_URL` in the production env (HF/Render secrets) and remove `RETRIEVAL_BACKEND`.
6. **Commit.** One commit in the `Aeropt-agent` repo, staging only the files listed above. Do NOT stage
   `memory/`, `tests/test_memory.py` or `EXTRACTION.md`. `local_ingest/syllabus.py` also holds the owner's
   chemistry-catalog edit, and committing it together is fine.

## Known, not fixed

- "solve the quadratic equation x^2-5x+6=0" resolves "ok" to an unrelated concept. The old pipeline did the
  same (an existing resolver weakness); maths is not in the corpus.
- The study overview and syllabus pages may now list chemistry chapters beside physics. There is no subject filter in the UI or services yet.
- Tutor prompts may still say "physics only".
