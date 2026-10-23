# Mock Test Agent — Architecture & Design Document

> **Audience:** Engineering leads and stakeholders who want to understand what was built, why each decision was made, and how the pieces fit together.
>
> **Where it lives:** everything is inside `mock_test_agent/`. The tutor server, shared config, and existing tests are not modified. The HTTP router is written but not mounted, and the SQL migration has not been applied.

---

## 1. Why a Dedicated Mock Test Agent?

The platform already has a **Study Room** — a conversational AI tutor that teaches concepts, quizzes students, and tracks their proficiency. But a mock test is a fundamentally different experience:

| Concern | Study Room | Mock Test Agent |
|---|---|---|
| Purpose | Learn concepts interactively | Simulate a real JEE exam |
| Questions | Curated by AI per conversation | Fixed set chosen upfront, timed |
| Answer reveal | Immediate feedback per question | Withheld until the full test is submitted |
| Marking | Positive only (learning) | JEE marking scheme (+3/−1, +4/−2 etc.) |
| Analytics | Per-concept proficiency (ongoing) | Score + chapter/concept breakdown (post-test) |
| LLM involvement | Every turn | One call at the end (debrief), cached |

Building mock tests on top of the study-room graph would mean fighting the graph's conversational assumptions at every step. Instead, the mock test pipeline is **a pure deterministic service layer** — no LangGraph, no streaming, no per-question LLM calls — that reuses existing retrieval and grading infrastructure without disturbing it.

---

## 2. High-Level Flow

```
Student (via frontend)
        │
        ▼
┌─────────────────────────────────────────────────┐
│     mock_test_agent/router.py  (not mounted)    │
│  GET  /mock/catalog   POST /mock/build           │
│  POST /mock/start     POST /mock/answer          │
│  POST /mock/submit    GET  /mock/result          │
└──────────┬──────────────────────────┬───────────┘
           │                          │
           ▼                          ▼
┌─────────────────────┐   ┌─────────────────────────┐
│  mock_test_agent/   │   │  mock_test_agent/       │
│  assembly.py        │   │  grading.py             │
│  (assembly)         │   │  (scoring & analytics)  │
└──────┬──────────────┘   └─────────┬───────────────┘
       │                            │
       ▼                            ▼
┌─────────────────────────────────────────────────┐
│          Supabase Postgres (user DB)             │
│  mock_tests · mock_test_questions               │
│  mock_test_submissions                          │
└──────────┬──────────────────────────────────────┘
           │ (existing infrastructure, unchanged)
           ▼
┌─────────────────────────────────────────────────┐
│  retrieval/search.py  ←─── pgvector corpus      │
│  services/answer_key.py ←── knowledge_private   │
│  services/grading.py  (commit_progress / WMA)   │
└─────────────────────────────────────────────────┘
           │  (after /submit, once per test)
           ▼
┌─────────────────────────────────────────────────┐
│  mock_test_agent/debrief.py                     │
│  One LLM call → narrative debrief               │
│  Cached in mock_tests.debrief                   │
└─────────────────────────────────────────────────┘
```

---

## 3. Database Schema

Three new tables were added via migration `028_mock_tests.sql`. **No existing tables were touched.**

### `public.mock_tests`
The top-level record for one test instance.

| Column | Purpose |
|---|---|
| `mock_test_id` | UUID primary key |
| `user_id` | Owner of this test |
| `exam` | `JEE_ADVANCED` / `JEE_MAIN` / `custom` |
| `config` | Full `MockTestConfig` as jsonb (subjects, chapters, time limit, marking scheme) |
| `status` | `built → in_progress → submitted → graded` |
| `started_at` / `submitted_at` | Timestamps for real elapsed-time tracking |
| `raw_score`, `max_score`, `accuracy_pct` | Aggregate scores (filled on submit) |
| `subject_scores`, `chapter_scores` | Per-section analytics jsonb |
| `debrief` | LLM narrative, generated on first `/result` request, cached forever |

### `public.mock_test_questions`
One row per question in the test. This table acts as a **snapshot**: question metadata and the official answer are copied here at build time. This means:
- Grading at submit time requires no cross-DB join to the retrieval corpus.
- Questions in the corpus can be updated without invalidating already-started tests.
- **The `answer` column is never sent to the frontend** — it only travels server-side.

| Column | Purpose |
|---|---|
| `content_id` | FK to the retrieval corpus |
| `display_order` | 1-based position in the test |
| `section` | Physics / Chemistry / Mathematics |
| `answer` | Official answer (jsonb, same structure as `knowledge_private.solutions`) |
| `has_gradekey` | False for subjective questions that cannot be auto-graded |

### `public.mock_test_submissions`
One row per student answer (written incrementally as they answer questions).

| Column | Purpose |
|---|---|
| `student_answer` | Raw text |
| `choice` | `["A","C"]` for MCQ; null for numeric |
| `numeric_value` | Parsed float for integer/numeric types |
| `grade_status` | `correct / incorrect / ungradable / unattempted` — **null until `/submit`** |
| `question_score` | Points after applying marking scheme (may be negative) |
| `answer_text` | Correct answer revealed after submit |

Row-Level Security is enabled on all three tables with **no public policies** — only the service role can read or write.

---

## 4. Data Models (`mock_test_agent/models.py`)

The Python layer mirrors the DB schema with clean dataclasses.

```
MockTestConfig
├── user_id, exam, total_time_minutes, marking_scheme
└── subjects: List[SubjectConfig]
             ├── subject ('Physics' | 'Chemistry' | 'Mathematics')
             ├── chapters (list of chapter_ids; empty = all chapters)
             ├── n_questions
             └── difficulty_target (1–5; None = exam default)

MockTestQuestion   — student-facing question (answer field never present)
MockTestSection    — list of questions grouped by subject
FeasibilityReport  — pre-build inventory check result
  └── gaps: List[FeasibilityGap]  — chapters that cannot be fully satisfied
MockTestState      — lightweight TypedDict for the debrief agent
```

`MockTestConfig` has `to_dict()` / `from_dict()` for jsonb round-tripping so the config stored in Supabase can be re-hydrated at any point without losing fidelity.

---

## 5. Assembly Pipeline (`mock_test_agent/assembly.py`)

When `POST /mock/build` is called, `assemble_test()` runs the following sequential pipeline:

```
assemble_test(MockTestConfig)
│
├─ 1. validate_config()
│      ─ exam must be JEE_ADVANCED / JEE_MAIN / custom
│      ─ subjects must be Physics / Chemistry / Mathematics
│      ─ n_questions ≤ 60 per subject
│      ─ 15 min ≤ time_limit ≤ 360 min
│      ─ no duplicate subjects
│      → raises MockTestConfigError (400) if anything fails
│
├─ 2. check_inventory()
│      ─ Counts gradeable PYQs in the retrieval DB per chapter
│        (content_type='PYQ', has_gradekey=True, not subjective/matching)
│      ─ Returns FeasibilityReport with per-chapter gaps
│      ─ If allow_fallback=True, short chapters borrow from adjacent ones
│      → No DB writes; read-only check against the retrieval corpus
│
├─ 3. select_questions() [per subject section]
│      ─ Fetches (n_questions × 1.4) candidates via retrieval/search.py
│        with content_type='pyq', require_solution=True
│      ─ Filters out subjective and matching types
│      ─ Caps at n_questions, sets display_order
│      ─ Attaches _answer_raw privately (never in the student dict)
│
└─ 4. persist to Supabase
       ─ INSERT into mock_tests (status='built')
       ─ INSERT into mock_test_questions (with answer snapshot)
       ─ Return assembled test dict (questions without answers)
```

The 1.4× candidate buffer (`_CANDIDATE_BUFFER_FACTOR`) exists because the retrieval search may return some candidates that fail the post-filter (wrong type, missing answer snapshot). Fetching more than needed ensures the final count is always met.

---

## 6. Grading Engine (`mock_test_agent/grading.py`)

### Marking Schemes

Two built-in schemes are defined. Either can be overridden per-test via `MockTestConfig.marking_scheme`.

| Question Type | JEE Advanced | JEE Main |
|---|---|---|
| Single Correct | +3 / −1 / 0 | +4 / −1 / 0 |
| Multiple Correct | +4 full / +1 partial / −2 wrong | +4 / 0 / −2 |
| Numeric (float) | +4 / 0 / 0 | +4 / −1 / 0 |
| Integer | +3 / 0 / 0 | +4 / 0 / 0 |
| Comprehension | +3 / −1 / 0 | +3 / −1 / 0 |

Partial credit for `multiple_correct` means: the student selected a correct subset with no wrong options chosen.

### Grading Sequence (triggered by `/submit`)

```
grade_all_submissions()
│
├─ For each question row in mock_test_questions:
│   └─ grade_submission()
│       ├─ If submission is None  → unattempted (score = 0)
│       ├─ If has_gradekey=False  → ungradable (score = 0)
│       ├─ Else: calls answer_key.grade_served_question()
│       │   (the same grader used by the Study Room)
│       └─ apply_marking_scheme() → final score
│
├─ commit_progress() per gradeable question
│   (updates the student's proficiency WMA for that concept — same path as Study Room)
│
└─ Bulk-upsert mock_test_submissions with grades
```

The grading is **idempotent**: a second `/submit` call returns the stored result without re-grading, preventing double-counting of proficiency updates.

### Analytics

After grading, three layers of breakdown are computed:

1. **Overall**: `raw_score`, `max_score`, `accuracy_pct`, `attempted`, `correct`
2. **Subject scores**: above, per Physics / Chemistry / Mathematics
3. **Chapter analytics**: accuracy and score per chapter_id
4. **Concept analytics**: accuracy per concept_id — the finest granularity

All analytics are stored in the `mock_tests` row as jsonb, so the result endpoint is a fast single-row read with no recomputation.

---

## 7. API Lifecycle

The six API routes correspond to the natural lifecycle of a test session:

```
GET  /api/v1/mock/catalog
     → Returns chapters with their gradeable PYQ counts
       so the frontend can build the chapter-selection UI
       and warn students if a chapter has too few questions.

POST /api/v1/mock/build
     → Validates config, selects questions, persists test.
       Returns assembled test (no answers). Status: built.

POST /api/v1/mock/start
     → Flips status to in_progress, records started_at.
       Returns ordered question list (answers still hidden).
       Rejects if already submitted.

POST /api/v1/mock/answer   (called repeatedly during the test)
     → Saves/overwrites one student answer. No grade revealed.
       Idempotent: re-submitting same content_id overwrites.
       Rejects if test is not in_progress.

POST /api/v1/mock/submit
     → Grades all answers, computes analytics, updates proficiency.
       Idempotent: second call returns stored result.
       Status: submitted → graded.

GET  /api/v1/mock/result/{mock_test_id}
     → Returns full analytics + optional LLM debrief.
       include_debrief=true triggers debrief generation (cached).
```

State machine enforced by the API:

```
built ──/start──▶ in_progress ──/submit──▶ submitted ──(grading)──▶ graded
                                                                        │
                                                         /result ◀──────┘
```

---

## 8. Debrief Agent (`mock_test_agent/debrief.py`)

The debrief is the only place an LLM is involved.

**Why defer it?** The student wants their score immediately after submitting. The narrative debrief can be generated asynchronously on the first `/result` call and cached in `mock_tests.debrief` forever. Subsequent fetches return the cached text — no LLM call.

**Why only one LLM call?** The analytics are already computed deterministically. The LLM is given a structured analytics table (subject scores, weak chapters, weak concepts) and asked to narrate it — not invent it. This keeps costs low and prevents hallucination of question numbers or statistics.

**Prompt inputs passed to the LLM:**
- Exam type, total score, subject breakdown table
- Chapter performance table (ranked by accuracy)
- Top 5 weakest concepts (from `build_concept_analytics`)
- Top 3 strongest chapters

**What the LLM produces:** A concise Markdown report with LaTeX for any formulae — no invented data, only narration of what the analytics show.

**Failure handling:** If the LLM call fails, the result endpoint still returns full analytics. The debrief field is simply null. Non-fatal by design.

**Follow-up integration:** `get_follow_up_recommendations()` extracts the weakest concept IDs from the analytics. These can be passed back to the Study Room to prioritise review sessions — closing the loop between mock tests and guided learning.

---

## 9. Security Design

| Concern | How it's handled |
|---|---|
| Answer leakage | `answer` stored only in `mock_test_questions` (service_role table, no public RLS policy). Never included in question dicts returned to frontend. |
| Cross-user access | Every query is filtered by `user_id`. Get/update calls reject if `user_id` doesn't match the test owner. |
| No REST exposure | `mock_test_questions` and `knowledge_private.solutions` have no public PostgREST policies — only reachable via the FastAPI service role. |
| Idempotent grading | Double-submit is detected by `status='graded'` check; returns cached result without re-calling `commit_progress()`. |
| Existing data unchanged | Zero modifications to existing tables. The migration only adds three new tables. |

---

## 10. Integration with Existing Platform

The mock test agent is **additive** — it reuses existing infrastructure and adds no dependencies to existing components:

| Existing Component | How Mock Tests Use It |
|---|---|
| `retrieval/search.py` | `select_questions()` calls the same search function used by the Study Room with `content_type='pyq'` |
| `services/answer_key.py` | `grade_submission()` calls `grade_served_question()` — the same grader the quiz flow uses |
| `services/grading.py` | `commit_progress()` updates the student's proficiency WMA exactly as quiz answers do |
| `utils/llm.py` | `complete_text()` used for the debrief — same LLM abstraction used by all agents |
| `config.py` | Read only for the debrief model name. Mock-test limits live in `mock_test_agent/settings.py`. |

The Study Room is not affected: its LangGraph graph, state, and endpoints are unchanged. This package is not imported by `api_server.py`.

---

## 11. File Map

```
mock_test_agent/
├── README.md                  ← how to run tests, and the two lines that mount the API later
├── MOCK_TEST_AGENT.md         ← this document
├── settings.py                ← limits, exam filters, debrief model
├── models.py                  ← MockTestConfig, questions, feasibility report
├── assembly.py                ← validate → inventory → select → persist
├── grading.py                 ← marking schemes, scores, chapter and concept analytics
├── debrief.py                 ← one LLM narrative, cached on the test row
├── router.py                  ← six routes, not included by the main app
├── migrations/
│   └── 028_mock_tests.sql     ← three new tables; not applied yet
└── tests/
    └── test_mock_test.py      ← 53 unit and integration tests
```

---

## 12. Test Coverage

53 tests across 10 test classes, all passing:

| Class | What's tested |
|---|---|
| `TestValidateConfig` | 13 edge cases for config validation |
| `TestResolveMarkingScheme` | Scheme selection for JEE_ADVANCED, JEE_MAIN, custom |
| `TestApplyMarkingScheme` | Per-question scoring: correct, incorrect, partial, unattempted |
| `TestAggregateScores` | Overall totals including negative-score scenarios |
| `TestAnalytics` | Chapter and concept breakdown accuracy |
| `TestFeasibilityReport` | Inventory gap reporting |
| `TestMockTestConfigSerialization` | Round-trip jsonb serialisation |
| `TestDebriefHelpers` | Offline helper functions (weak concepts, strong chapters) |
| `TestMockTestIntegration` | Full pipeline with mocked DB: build, grade correct/incorrect MCQ, handle ungradable questions |
| `TestEdgeCases` | Empty inputs, uppercase normalisation, zero-question tests |

The product always talks to Supabase. When a student builds a test, questions are read from the retrieval database (`public.content` joined to `knowledge_private.solutions`) and the assembled test is written to `mock_tests` / `mock_test_questions`. The 53 automated tests do not open that connection: they stub the environment variables and mock the database so they can run in CI without live credentials.
