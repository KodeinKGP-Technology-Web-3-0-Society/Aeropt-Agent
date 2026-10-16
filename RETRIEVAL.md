# Local retrieval pipeline

Everything the tutor knows, derived from sources by a script, with a retrieval path
that answers in single-digit milliseconds and refuses to invent grounding.

Selected by `RETRIEVAL_BACKEND=local` in `.env.local`. The deployed service does not
set it, so Neo4j Aura + Gemini embeddings remain exactly as they were.

```bash
make index            # derive syllabus + corpus, embed, write the index (~4 s)
make index-fetch      # same, downloading any missing OpenStax modules first
make retrieval-bench  # time a standard query set
.venv/bin/python -m retrieval.cli "why does a rolling ball not slip"
.venv/bin/python -m retrieval.cli --type practice --user u1 "friction questions"
.venv/bin/python -m retrieval.cli --status
```

## What changed, in numbers

| | before | now |
|---|---|---|
| Syllabus concepts | 33, hand-mapped | **1016 derived**, each with a textbook definition |
| Chapters | 3 | **44** (42 mapped to JEE units, 2 flagged beyond syllabus) |
| Retrievable items | 203 | **10,004** |
| Questions with stored solutions or answer keys | ~160 | **3,519** |
| Exam-paper questions usable | 134 of 818 | **361**: 120 official JEE Advanced (JEE-Bench) + 241 coaching test-paper questions answerable from text |
| In-scope concepts with no material | 20 | **0** (and **0** of 239 JEE topics) |
| Warm retrieval latency | ~1700 ms (Gemini embed + Aura) | **~6-11 ms** |
| Query-time network calls | 2 | **0** |
| Concept resolution | whole taxonomy in an LLM prompt | embeddings + aliases, **~0.3 ms**, no LLM |
| Corpus rebuild | minutes (per-item embed API) | **~4 s** |

## Sources

Nothing is hand-extracted except `reference_notes.py` (below). `local_ingest/` downloads and parses:

| Source | What it gives | Licence |
|---|---|---|
| OpenStax University Physics (322 CNXML modules) | 951 theory sections, 487 worked examples with solutions, 5001 exercises, 1024 glossary definitions, 270 chapter summaries, the chapter/section tree, cross-reference links | CC BY-NC-SA 4.0 |
| HuggingFace JEE Main/Advanced set | 818 questions from **coaching test papers** (P-01 … Ph-28, a teacher's Advanced sheet), not NTA papers; 241 kept (see below) | coaching test papers |
| JEE-Bench | 515 JEE Advanced questions with verified gold answers (physics subset: 120) | past exam papers |
| `local_ingest/reference_notes.py` | 39 authored notes: JEE topics no source teaches (lab experiments, bridges, capillarity, rectifiers…) and the JEE optics sign convention | original |

`ncert.nic.in` is unreachable from this machine (TLS handshake fails), which is why
OpenStax is the theory source. **The licence is non-commercial**: fine for local
development, not for a paid product. Swapping the source means writing one parser —
every item carries `source_book`, `source_ref` and `license`, and the syllabus is
derived from structure, so nothing downstream is hard-wired to OpenStax.

## Exam level: Main and Advanced, not Advanced-only

| | JEE Main | JEE Advanced | labelled for both |
|---|---|---|---|
| Official PYQs (JEE-Bench) | — | 120 (answer keys) | — |
| Coaching test-paper questions | 210 | 31 | — |
| Practice questions | — | — | 4,967 |
| Theory / worked examples | — | — | 3,756 / 486 |

`target_exam` filters PYQs exactly (a Main request never returns an Advanced paper).
The 4,967 textbook exercises carry no exam label, so what separates the two exams there
is the **difficulty band**: `EXAM_DIFFICULTY_TARGET` aims Main at 3 and Advanced at 4,
and `search._rank` keeps only questions within ±1 of that target whenever enough exist.
Measured over 6 concepts × 5 results: Main `{2:7, 3:22, 4:1}`, Advanced `{3:25, 4:5}`.
A learner-specific `difficulty_target` from the planner overrides the exam default.

Official PYQ depth (120 Advanced, 0 Main) is a **source** limit, not a pipeline one. The readily
available Advanced sets — e.g. the MIT-licensed `Reja1/jee-neet-benchmark` — ship the
papers as page scans, so adding them needs an OCR pass before ingest. The 348
challenge-tier textbook problems are what currently carries Advanced-level practice.

## Official papers: questions and answers never mix

Built on the extraction engine in `local_ingest/extract/` — design, research and phases in
[`EXTRACTION.md`](EXTRACTION.md); NCERT (`make ncert`) uses the same engine.

`make exams` (`local_ingest/exams/`) ingests official JEE Advanced papers. Two documents
per paper, read by two different methods, joined only when the join is proved:

| step | input | how | never sees |
|---|---|---|---|
| questions | clean question paper (jeeadv.ac.in archive) | page image → Cursor vision model (`cursor_gateway/vision_once.mjs`), transcribe-only prompt, cached per page | the key |
| answers | official **final** key (live, or the Internet Archive's capture of the official URL) | regex over the key PDF's own text layer — no model | the extraction |
| join | both | `(year, paper, subject, qno)` + checks below | — |

A question is kept only if: the key has a gradable answer (bonus/dropped questions are
not); the key document's own copy of the question matches the extracted stem (catches a
numbering slip); the answer's form fits the section type (one letter for single-correct,
letters for multiple-correct, number/range for numeric); an MCQ has four options; and a
question that refers to a figure has one described. Everything else is listed with its
reason in `.local-run/sources/exams/report.json`. Final-key rulings that accept more than
one answer ("A or B", "[75 to 79] OR [94 to 95]") are kept as such.

Physics pages are found without reading the whole paper: the text layer shows where
numbering restarts at Q.1 (one block per subject, order varies by year); the first page of
each block is read to name its subject; only the Physics block is read in full (~13 pages).

Coverage: `local_ingest/exams/catalog.py` lists 2014–2017, 2019 and 2022–2026 — 322 Physics
questions. Keys the text layer cannot give (scanned 2015–16, font-scrambled 2022, 2023's
answers printed in the blank) are read from the key's page images, gaps only, each answer
joined only to the question its printed opening matches. 2022's one key file is sliced per
paper; where it prints a value and an accepted range, the range is the answer. Matrix-match
questions (2015) keep a row-by-row answer the tutor grades (no instant grading). The Internet
Archive holds the 2007–2013, 2018, 2020 and 2021 papers but no answer key for them, and
unkeyed questions are not ingested.

Adding coaching solutions (e.g. ALLEN) is a separate, later layer: a solution is attached
by the same `(year, paper, qno)` key and only if its final answer equals the official key —
and only with the provider's written permission recorded as the item's licence.

## The syllabus is derived, not typed

`local_ingest/syllabus.py` builds the graph from the book's own structure:

- **chapters and reading order** from `collections/*.collection.xml`;
- **concepts** from `<glossary><definition>` — a real term with a real definition —
  falling back to section titles where a module has no glossary;
- **prerequisites** from `<link document="...">` cross-references, kept only when the
  target sits *earlier* in reading order. That rule makes the graph acyclic by
  construction rather than by cycle-breaking, and the test suite asserts it.
- **JEE scope** from `local_ingest/jee_syllabus.py`, the one hand-written file:
  20 units, 29 chapter names, 218 aliases. It is scope metadata, not content — it is
  what lets the tutor say "that is beyond your syllabus" instead of guessing.

Concept assignment for each chunk is embedding-based within the candidate set the
book structure already implies (the module's concepts, then the chapter's). No keyword
tables: the previous pipeline needed 33 hand-written keyword groups plus 16
hand-written "gap cards" and still covered three chapters.

## The retrieval path

```
query
  ├─ gate        deterministic: retrieve | reuse | skip | clarify        ~0.1 ms
  ├─ resolver    aliases → chapter → material vote → nearest concept     ~0.3-10 ms
  ├─ scope       concept → +prerequisites → +chapter, widened until the
  │              candidate pool is big enough to rank meaningfully
  ├─ BM25        FTS5 over the candidate set only                        ~1-3 ms
  ├─ dense       numpy dot over the candidates' vectors                  ~0.1 ms
  ├─ fusion      reciprocal rank fusion, primary-concept boost, difficulty targeting
  ├─ novelty     drop what this student has already been served, by id and fingerprint
  └─ diversity   MMR, so five paraphrases of one paragraph don't fill the answer
```

Two files, two lifetimes: `.local-run/index/corpus.sqlite3` is rebuilt by every
ingest, `.local-run/index/ledger.sqlite3` never is. That is why "don't show me that
question again" survives a corpus rebuild.

Embeddings are static (`minishlab/potion-base-8M`, 256-d, snapshotted into
`.local-run/models` so a query never needs the network). A static model is weaker per
vector than Gemini, which is why it is never asked to work alone — the concept graph
and BM25 carry precision, dense similarity carries recall, and rank fusion combines
them. `AEROPT_EMBED_BACKEND=gemini` switches the whole pipeline back to hosted
embeddings if you want matched vectors; the index records which model built it.

## How it avoids hallucinating

1. **Unresolved stays unresolved.** A query nothing in the syllabus matches returns no
   material and a note telling the tutor to say so. It never falls back to "the
   nearest concept" and dresses an off-topic answer in real physics.
2. **Grounding is graded, not binary.** Resolution returns `strong` / `weak` / `none`.
   Calibration showed concept-name similarity *cannot* separate physics from other
   subjects (physics 0.37-0.88, other subjects 0.16-0.42, overlapping); similarity to
   the best-matching chunk separates far better (0.51-0.89 vs 0.22-0.52). `weak`
   material reaches the tutor with an explicit "this may not be what they asked".
3. **Every chunk is citable.** `citation` is book + chapter + section, or the exam
   paper and question id. A claim with no citation behind it is visibly the model's
   own recall.
4. **Questions carry verified solutions.** 3,987 questions have stored solutions or
   answer keys, and `require_solution` prefers them, so "practice" does not mean
   "invent a question and hope the answer is right".
5. **Out-of-syllabus content is flagged, not hidden.** Relativity and particle physics
   stay in the corpus with `in_syllabus = 0` and come with a note.
6. **No question is served without what it needs.** The HF set references figures it
   does not ship; 517 of its 818 questions said "the following circuit" with no circuit,
   and a text-only model invents one. Those, questions with image options, and ones
   interleaved with Kruti Dev mojibake are dropped at ingest; solutions that lost their
   working to images are cut back to the answer key. OpenStax figures carry
   accessibility text, which `cnxml.py` now inlines as `[Figure: …]` (765 questions
   rely on it). Figure text is kept out of embeddings, FTS and fingerprints
   (`corpus.match_text`): it is for the reader, and a photo caption otherwise drags
   off-topic queries past the resolver's guard.
7. **Provenance is not invented.** Coaching test-paper questions are cited as such,
   and the pack tells the tutor not to name a JEE year or shift for them.
8. **Conventions are stated, not assumed.** OpenStax optics is real-is-positive
   (1/d_o + 1/d_i = 1/f); JEE is Cartesian. Any optics retrieval carries a note with
   the JEE forms, and a reference note gives the conversion.
9. **Every syllabus topic has a passage.** Topics no source teaches get a short note in
   `local_ingest/reference_notes.py`. It is authored text, so it is held to a higher bar:
   standard results only, each with its conditions, and changed only with a source in
   hand. Questions served without a stored answer come with a note to solve and check
   before grading.

## How it avoids repeating a question

`store.served` records every question shown to a student — content id, concept,
fingerprint, timestamp, and a count. Retrieval excludes them **before** ranking, both
by id and by a shingle fingerprint, so the same problem reworded by another source
still cannot come back. The gate also detects "another one" / "next question" and
marks the turn as novelty-required even for a theory request.

When a concept runs dry the pipeline says so rather than quietly recycling:

| field | meaning |
|---|---|
| `concept_unseen_remaining` / `concept_question_total` | the honest budget for this concept |
| `concept_pool_exhausted` | every question on this concept has been served |
| `borrowed_from_neighbours` | these came from sibling concepts in the chapter — tell the student |
| `pool_exhausted` | even the wider pool is drained; the items are the least recently seen |

`service.forget(user_id[, concept_id])` clears the ledger for a fresh revision cycle.

## When retrieval is skipped

`retrieval/gate.py` decides before any model call, and returns four outcomes rather
than a yes/no:

| turn | decision |
|---|---|
| "hi there", "thanks, that helped!", "ok got it" | `skip` — nothing to ground |
| "what is 45*3", "calculate 12.5 / 4" | `skip` — the calculator answers it |
| "(b)", "9.8 m/s^2" while a question is pending | `skip` — that is an answer being submitted |
| "explain that again but shorter", "why was my answer wrong?" | `reuse` — the material in hand covers it |
| "and this one?" | `reuse`, but re-checked after resolution: if the concept moved, it retrieves |
| "give me another question" | `retrieve` with novelty enforced |
| anything substantive | `retrieve` |

The old gate asked an LLM "is this a meta turn?" on every doubt turn — a network round
trip to save a network round trip. Rules decide here, and when they are unsure they
default to retrieving, because a needless retrieval costs ~10 ms while a missed one
costs grounding. `AEROPT_GATE_LLM=1` restores the classifier for the ambiguous middle.

## Files

| file | role |
|---|---|
| `local_ingest/fetch_openstax.py` | download the CNXML bundle |
| `local_ingest/mathml.py` | MathML → LaTeX |
| `local_ingest/cnxml.py` | one module → sections, examples, exercises, glossary |
| `local_ingest/jee_syllabus.py` | JEE units, chapters, aliases (scope metadata) |
| `local_ingest/syllabus.py` | chapters, concepts, prerequisite graph, JEE mapping |
| `local_ingest/reference_notes.py` | authored notes for topics no source teaches, and the JEE optics convention |
| `local_ingest/corpus.py` | every item from every source + concept mapping |
| `local_ingest/push_index.py` | orchestration, builds corpus + embeddings into Postgres (`make push`) |
| `retrieval/embedder.py` | static embeddings (`minishlab/potion-base-8M`, 256-d) |
| `retrieval/store.py` | Postgres schema pool, in-memory caches, queries |
| `retrieval/gate.py` | does this turn need retrieval |
| `retrieval/resolver.py` | query → concept, with graded evidence |
| `retrieval/search.py` | scope → pgvector + tsvector → fusion → novelty → MMR |
| `retrieval/service.py` | the public API and the grounding pack |
| `retrieval/bridge.py` | GraphRAG-shaped adapter for the existing graph |
| `retrieval/local_graph.py` | Graph client over Postgres for follow-ups, suggestions, and `search_knowledge_graph` |
| `retrieval/cli.py` | inspect, bench, clear a ledger |
| `tests/test_retrieval_local.py` | the four guarantees, asserted |

## LaTeX that renders

Corpus text and model output both reach the same renderer, so both are held to one
contract: **every math span must parse, and no valid maths may be lost.**

| stage | where | what it does |
|---|---|---|
| ingest | `local_ingest/corpus.latex_safe` | fixes source quirks (`\mathcal{l}`, `^\circC`, `\mspace`, double subscripts, `½`, `ℏ`), converts `\(…\)`/`\[…\]`, delimits bare TeX in prose, escapes prices, balances delimiters — and strips delimiters from the few chunks that cannot be repaired |
| outbound | `agents/solver.prepare_student_markdown` | the same rules on model output, plus `harden_math_spans`: a span that will not parse ships as text, not as a red error |
| render | `Aeropt-frontend/src/lib/math/mathHarden.ts` | KaTeX itself vetoes each span, and the preprocessing pipeline that scores worse is discarded |
| guarantee | `Aeropt-frontend/src/lib/math/rehypeKatexSafe.ts` | runs after rehype-katex: any error node left becomes the formula as `code`, so a red error cannot reach a student |

Measured with real KaTeX over 1,900 corpus samples and 24 adversarial agent outputs
(`cd ../Aeropt-frontend && npm run test:latex`):

| | failures |
|---|---|
| before | 386 broken spans, 219 documents with a swallowed paragraph |
| after (span audit) | **0 of ~5,400 spans**, 0 unbalanced documents |
| after (real remark-math → rehype-katex render) | **0 error nodes** in 1,504 documents / 5,359 rendered formulas |

The classes that were breaking: a price in prose (`$0.0900 per kWh`) opening a math
span and eating the paragraph; `^\circC` parsing as one undefined macro (every
temperature in the corpus); `\text{log}` losing operator spacing (1,253 spans); braces
from MathML fences left unbalanced (54 piecewise definitions); `\mathcal{l}` for ℓ (116
spans); and `$1036$` in a table of values being mistaken for money.

Two rules earn their keep. **Escapes are not braces** — counting `\{` as a group
reported every valid `\left\{ … \right.` as broken. And **preprocessing may not make
a message worse** — the wrapping heuristics have to guess where maths starts in prose,
so both pipelines run and KaTeX picks the winner.

## The rest of the agent

All graph and search call sites — follow-up suggestions, `generate_suggestions`, quiz step
decomposition, `search_knowledge_graph`, and tutor tools — query Postgres directly through
`retrieval.local_graph.graph_client()` and `retrieval.store`. Neo4j Aura and local SQLite indices
have been completely removed.

The single embedding model is `minishlab/potion-base-8M` (`DIM = 256`), enforced in
`retrieval/embedder.py` via `to_pg()`. Queries and chunks exist in the exact same vector space.

## Rebuilding after changing a source

```bash
make push             # builds/updates local Postgres (or make push-dry)
.venv/bin/python -m pytest tests/test_retrieval_local.py
```

The build prints per-unit coverage, weak concept mappings and any in-scope concept
left with no material — read that report, it is the quality signal.

## Postgres + pgvector corpus (`make push`)

`local_ingest/push_index.py` builds the corpus **directly into Postgres** (`RETRIEVAL_DATABASE_URL`) —
derives syllabus → items → concept mapping → embeddings, without any intermediate SQLite or `.npy` files.

- Schema: `db/migrations/019_retrieval_pgvector.sql` (additive, idempotent; run on the *retrieval* project).
  `content.embedding` / `concepts.embedding` are `vector(256)` with HNSW cosine indexes (`hnsw.ef_search=100`);
  `embed_model` is stored per row with a CHECK constraint.
- Embeddings are generated during ingestion using the explicit static model (`minishlab/potion-base-8M`),
  guaranteeing exact embedding space alignment with runtime queries.
- `RETRIEVAL_DATABASE_URL=postgresql://… make push`. Rows are written only when their content hash changes,
  so a re-push of an unchanged corpus writes 0 rows. `make push-dry` diffs and rolls back. `PRUNE=1` deletes
  rows of the pushed subjects that the build no longer produces. Non-localhost URLs require `ALLOW_HOSTED=1`.
- Subjects: Physics (12,933 items) and Chemistry (NCERT Class XI & XII, 19 chapters, 405 concepts, 1,941 items).
  Concepts are derived strictly per chapter, ensuring 0 cross-subject links.
- Full pipeline in Postgres: `gate` → `resolver` (dense cosine similarity against `concepts.embedding`) →
  `search` (pgvector cosine distance for dense scoring + `ts_rank_cd` over `content.fts` for lexical scoring,
  fused via Reciprocal Rank Fusion) → novelty check (`served_items` in user DB) → MMR re-ranking.
- High performance: in-process caching of concepts, chapters, and topics in `retrieval/store.py` with automatic
  invalidation when `retrieval_meta.built_at` changes; typical query round trips take ~10–15 ms locally.
