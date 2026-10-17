# Document extraction — plan and design

A local "context.dev for exam documents": point it at official PDFs, get validated,
provenance-stamped records the corpus can trust. Built for Physics; nothing in the engine
is Physics-specific.

## Why not a hosted web-extraction API

Services like Context.dev turn **web pages** into Markdown or schema-shaped JSON. Our inputs
are **PDFs full of mathematics and figures**, and our hard problems are ones a generic
scraper does not attempt: LaTeX that renders, figures described in words, and joining a
question to its official answer without ever mixing the two. Their anti-bot and proxy
features are also beside the point — the sources here are official and public, and a
source that needs evasion is a licensing question, not an engineering one.

## Research findings (2026-10-03)

| Source | Reachable | Format | Answers | Licence | Decision |
|---|---|---|---|---|---|
| JEE Advanced papers, jeeadv.ac.in/archive (2007–2025) + current-cycle docs | yes | 2019+ text PDFs (maths garbled in the text layer); 2007–2018 mostly scanned | official final keys: live for the current cycle, Internet Archive captures for 2014–16, 2019, 2022–26; 2017 likely `P1_2017.pdf` | public exam documents | **ingest** (collection `jee_advanced`, live) |
| JEE Main, NTA | site yes; papers no | papers are per-candidate, behind login; public keys list question IDs → option IDs only | IDs are useless without the paper | — | **blocked on NTA site**; alternative: 2,311 digital PYQs + 39 papers available via JEE Challenger API (see [`JEE_CHALLENGER_API.md`](local_ingest/exams/JEE_CHALLENGER_API.md)) |
| JEE Challenger API (`jeechallenger.com`) | yes (NextAuth session) | JSON with KaTeX LaTeX math, options, diagrams | official embedded keys + full step-by-step solutions | student portal | **extractable** (see [`local_ingest/exams/fetch_jeechallenger.py`](local_ingest/exams/fetch_jeechallenger.py)) |
| NCERT Physics XI/XII (rationalised), `ncert.nic.in/textbook/pdf/{keph,leph}{1,2}NN.pdf` | yes (direct PDF URLs; the index page fails TLS) | text PDFs; prose clean, **every symbol-font glyph wrong** (`VµI` for V ∝ I, `W` for Ω, `r` for ρ), sidebar captions interleaved | worked examples carry their solutions; exercises have none | © NCERT — fine for local development; commercial use needs NCERT's permission | **ingest** (collection `ncert`) |
| Sponsor books (Arihant, GK Publications, Bharati Bhawan) | publisher files pending | — | answer keys in-book | sponsor agreement | collection to add when files arrive |
| Coaching solutions (e.g. ALLEN) | public PDFs | text | their own solutions | ALLEN's copyright | attach-only layer, after written permission |

Two measured facts shaped the engine:

* **Text layer vs vision.** Text extraction is free and exact for prose and for answer keys,
  and wrong for nearly every equation. So the engine scores each page's text layer and reads
  it by vision only when it must; keys are always read from text.
* **Cursor throughput.** One vision call ≈ 35–50 s whatever the content; four parallel calls on
  one key draw `Network request failed`, two do not. Budget ≈ 45 s × pages ÷ 2. Everything is
  cached per page, so re-runs and re-validation are free.

## Design

```
local_ingest/extract/          the engine (source-agnostic)
  fetch.py      download with Internet Archive fallback; %PDF check; sha256; meta.json
  pdf.py        per-page text layer, page render, text-quality score
  reader.py     Cursor vision/text calls: cache, retries with backoff, bounded concurrency
  report.py     kept / dropped-with-reason bookkeeping; JSONL merge per document

local_ingest/exams/            collection: official JEE Advanced papers + keys
local_ingest/ncert/            collection: NCERT Physics XI + XII
```

A **collection** supplies four things and nothing else:

1. **catalog** — the documents (URL, archive capture, licence, which syllabus chapter).
2. **prompt + version** — what a page should become, as JSON. The version is part of the cache
   key, so changing a prompt re-reads only what it affects.
3. **stitch** — page results → records (questions, sections, examples), carrying text across
   page breaks.
4. **validate** — every record either passes all checks or is dropped with a reason in
   `report.json`. Nothing is repaired by guessing.

### Guarantees (enforced in code, asserted in tests)

| guarantee | how |
|---|---|
| a question never carries a wrong answer | questions and keys come from different documents by different methods (vision vs text layer); joined only on `(year, paper, subject, qno)` and only if the key's copy of the question matches the extracted stem and the answer's form fits the question type |
| nothing references a figure the reader cannot see | a record that mentions a figure must carry a figure description, else it is dropped |
| maths renders | every record's text passes `utils.latex_guidelines.find_latex_issues`; failures are dropped, not shipped |
| every record is traceable | source URL, file sha256, page numbers, reading method, model and prompt version |
| re-runs cost nothing | cache keyed by (prompt version, model, page image) |
| a failure is local | one bad page/document is reported and skipped; the rest of the run completes |

### Lesson from the full NCERT run: check the reader against the page

The first NCERT prompt showed sample values (`"number": "3.4", "title": "OHM'S LAW"`). On some
pages the model copied them — and on one it wrote an entire plausible page of Current
Electricity into *Work, Energy and Power*. Nothing in the JSON looked wrong. What caught it: the
PDF's own text layer, which garbles symbols but keeps words and numbers. Now every block must
have ≥ 60 % of its words printed on its page, every heading/example/exercise number must be printed
there, failing pages are re-read once with a placeholder-only prompt (version `n2`), and what still
fails is dropped. 47 of 613 pages (7.7 %) were flagged; 12 blocks were dropped after re-reading.
Pages with no usable text layer (keph107) cannot be checked this way and are marked as such.
Prompts must show placeholders, never example values.

### Data extracted elsewhere: H.C. Verma (`local_ingest/hcverma.py`)

`.local-run/CLASS XII&Xi/` holds 756 rows from 13 H.C. Verma chapters, extracted by a model outside this
pipeline, with the source PDFs. Measured against those PDFs, the file mixed three things: example
questions near-verbatim (median 96 % of 3-word phrases printed in the book), example solutions mostly
rewritten (40 %), theory mostly paraphrased (42 %). One rewritten solution answered a different problem
(−3GM/2a for the book's −3GM/a). So each part has its own test — questions must be printed in the book;
a rewritten solution must end on a distinctive number found in the book's own solution to that example
(within 2 %, 1,200 characters after the question); theory must be verbatim or a close paraphrase — and a
chapter without its PDF (Oscillations) is not used. Result: 252 confirmed worked examples, 107 questions
kept without a solution, 288 theory passages; 107 rows not used.

### H.C. Verma chapters with PDFs only (`local_ingest/hcverma_pdf.py`)

Eight folders had the book's pages but no extracted rows: 12 book chapters (17 Light Waves, 18–20
geometrical optics / instruments / dispersion, 38 EMI, 39 AC, 40 EM waves, 42 photoelectric, 43 Bohr
atom, 44 X-rays, 45 semiconductors, 46 nucleus). They go through the NCERT engine with a book-specific
prompt (`stitch` + text-layer grounding). Pages belong to the chapter whose printed `CHAPTER nn` marker
precedes them, not to the folder (the EM Waves file opens with the end of chapter 39). These files hold
theory and worked examples only — they stop at "Questions for short answer", whose answers are not
in them. Theory needs >= 85 % of its words printed on its pages (that caught the model writing its own
DC-motor paragraph); a page with a failing block is re-read once (`h2`), and per chapter the reading
with more checked records is kept. Result: 176 theory sections, 197 worked examples with the book's
own solution, 20 exercises; 56 blocks dropped with reasons (`report_pdf.json`). `make hcverma` runs
both H.C. Verma steps.

### NCERT specifics

* Page JSON is a list of **blocks** in reading order: `heading` (with section number),
  `text`, `example` (question + solution, split at "Solution"), `exercise`, `figure`
  (caption + a description of the drawing), `box` (sidebar/biography — kept out of theory),
  `summary`, `points_to_ponder`.
* Stitching joins blocks across pages into **sections** (theory chunks of ~1,500 chars under
  their heading path), **worked examples** (with solutions → solved examples) and **exercises**
  (no answers → practice without stored solution; the retrieval note already tells the tutor
  to solve and check before grading).
* Chapters map onto the existing syllabus chapters (`catalog.py`), so NCERT text joins the
  same concepts as OpenStax; ranking prefers NCERT over OpenStax (`SOURCE_WEIGHT`).
* NCERT uses the Cartesian sign convention, so once optics is ingested the tutor's material
  agrees with the convention note instead of contradicting it.

## Phases

| phase | scope | status |
|---|---|---|
| 1 | engine: fetch, pdf, reader, report; `exams` ported onto it, cache-compatible | this change |
| 2 | `ncert` collection: catalog, prompt, stitch, validate; corpus loader; ranking | done |
| 3 | full NCERT run (613 pages; 4 keys × 2 calls) | done — 1,066 records, text-layer checked |
| 4 | more JEE Advanced years: 2014–17, 2019, 2022 added; key-image pass for scanned/scrambled keys and 2023's in-blank answers; matrix-match keys | done — 322 questions; 2007–13, 2018, 2020–21 have no archived key |
| 5 | sponsor books as collections when publisher files arrive; ALLEN solutions as an attach-only layer after permission | blocked on agreements |

## Operating it

```bash
make exams                          # JEE Advanced (YEARS="2025 2026" to limit)
make ncert CHAPTERS="leph103"       # one NCERT chapter; omit CHAPTERS for all
make index                          # fold verified records into the retrieval index
```

Cursor key: `CURSOR_EXTRACT_API_KEY` in `cursor_gateway/.env` (gitignored).
Tuning: `CURSOR_EXTRACT_MODEL` (default `composer-2.5`), `CURSOR_EXTRACT_CONCURRENCY` (2).
