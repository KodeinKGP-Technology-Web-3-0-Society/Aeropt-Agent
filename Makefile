.PHONY: push push-dry push-fetch cursor-gateway cursor-gateway-test run-cursor ci test deploy retrieval-bench latex-fixture exams ncert hcverma extract-status eval eval-live

ci:
	bash ci/run.sh

test:
	bash ci/run.sh

eval:
	.venv/bin/python -m evals.runner --mode replay

eval-live:
	.venv/bin/python -m evals.runner --mode live --with-judge

deploy:
	bash ci/deploy-local.sh

# Official JEE Advanced papers + official final keys -> verified questions
# (local_ingest/exams). Page images are read by a Cursor model (CURSOR_EXTRACT_API_KEY in
# cursor_gateway/.env); answers come only from the key's text layer. Cached: a re-run
# only pays for new pages. Then `make push`. YEARS="2025 2026" limits the run.
exams:
	.venv/bin/python -m local_ingest.exams $(if $(YEARS),--years $(YEARS),)

# NCERT Physics XI + XII -> theory, worked examples, exercises (local_ingest/ncert, same
# engine as exams). ~625 pages; `.venv/bin/python -m local_ingest.ncert --estimate` first.
# CHAPTERS="leph103 leph201" limits the run. Then `make push`.
ncert:
	.venv/bin/python -m local_ingest.ncert $(if $(CHAPTERS),--chapters $(CHAPTERS),) $(if $(SUBJECT),--subject $(SUBJECT),)


# H.C. Verma: verify the sponsor's CSV rows, then read the PDF-only chapters (same reader).
hcverma:
	.venv/bin/python -m local_ingest.hcverma
	.venv/bin/python -m local_ingest.hcverma_pdf

# Live progress of exams/ncert runs, from the caches:  watch -n 30 make extract-status
extract-status:
	@.venv/bin/python -m local_ingest.extract.status

# The retrieval corpus: derive (physics: OpenStax, chemistry: NCERT) -> embed with
# retrieval/embedder.py (the only model) -> upsert into the retrieval Postgres
# (RETRIEVAL_DATABASE_URL). There is no local index. A non-localhost URL needs ALLOW_HOSTED=1.
# Unchanged rows are not rewritten. `make push-dry` diffs and rolls back.
push:
	.venv/bin/python -m local_ingest.push_index $(if $(ALLOW_HOSTED),--allow-hosted,) $(if $(PRUNE),--prune,) $(if $(SUBJECT),--subject $(SUBJECT),)

push-dry:
	.venv/bin/python -m local_ingest.push_index --dry-run $(if $(ALLOW_HOSTED),--allow-hosted,) $(if $(PRUNE),--prune,)

# Same, downloading any missing OpenStax modules first.
push-fetch:
	.venv/bin/python -m local_ingest.push_index --fetch $(if $(ALLOW_HOSTED),--allow-hosted,)

retrieval-bench:
	.venv/bin/python -m retrieval.cli --bench

# Dump corpus text (as the student would see it) into the frontend's KaTeX fixture,
# then run the contract there:  cd ../Aeropt-frontend && npm run test:latex
latex-fixture:
	.venv/bin/python -m local_ingest.latex_fixture --limit 1200

# --- Run the tutor on Cursor models instead of Groq (opt-in; see cursor_gateway/README.md) ---
cursor-gateway-test:
	cd cursor_gateway && npm test

cursor-gateway:
	bash cursor_gateway/run.sh gateway

run-cursor:
	bash cursor_gateway/run.sh agent
