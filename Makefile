.PHONY: ci test deploy ingest-local index index-fetch retrieval-bench latex-fixture

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

retrieval-bench:
	.venv/bin/python -m retrieval.cli --bench

# Dump corpus text (as the student would see it) into the frontend's KaTeX fixture,
# then run the contract there:  cd ../Aeropt-frontend && npm run test:latex
latex-fixture:
	.venv/bin/python -m local_ingest.latex_fixture --limit 1200
