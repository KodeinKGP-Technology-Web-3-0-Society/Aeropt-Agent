.PHONY: cursor-gateway cursor-gateway-test run-cursor ci test deploy ingest-local index index-fetch retrieval-bench latex-fixture exams ncert extract-status

ci:
	bash ci/run.sh

test:
	bash ci/run.sh

deploy:
	bash ci/deploy-local.sh

ingest-local:
	.venv/bin/python -m local_ingest.run

# Local retrieval index: derive the syllabus + corpus from the sources, embed, write
# .local-run/index and mirror public.content. Rebuilds in seconds; never touches Aura
# or a hosted Supabase project.
index:
	.venv/bin/python -m local_ingest.build_index

# Same, but download any missing OpenStax modules first.
index-fetch:
	.venv/bin/python -m local_ingest.build_index --fetch

# Official JEE Advanced papers + official final keys -> verified questions
# (local_ingest/exams). Page images are read by a Cursor model (CURSOR_EXTRACT_API_KEY in
# cursor_gateway/.env); answers come only from the key's text layer. Cached: a re-run
# only pays for new pages. Then `make index`. YEARS="2025 2026" limits the run.
exams:
	.venv/bin/python -m local_ingest.exams $(if $(YEARS),--years $(YEARS),)

# NCERT Physics XI + XII -> theory, worked examples, exercises (local_ingest/ncert, same
# engine as exams). ~625 pages; `.venv/bin/python -m local_ingest.ncert --estimate` first.
# CHAPTERS="leph103 leph201" limits the run. Then `make index`.
ncert:
	.venv/bin/python -m local_ingest.ncert $(if $(CHAPTERS),--chapters $(CHAPTERS),)

# Live progress of exams/ncert runs, from the caches:  watch -n 30 make extract-status
extract-status:
	@.venv/bin/python -m local_ingest.extract.status

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
