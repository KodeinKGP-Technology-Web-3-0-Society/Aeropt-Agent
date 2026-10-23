-- 028_mock_tests.sql
-- Dedicated Mock Test system: timed, multi-chapter, auto-graded tests for
-- Physics, Chemistry, and Mathematics (JEE Main / Advanced / custom pattern).
--
-- Three tables, no changes to existing tables:
--   mock_tests            one row per test instance (config, status, scores)
--   mock_test_questions   one row per question assembled into the test
--   mock_test_submissions one row per student answer (graded after submit)
--
-- Only the service role touches these. RLS is enabled with no public policies.
-- Idempotent: safe to run again on the same database.

-- ─── mock_tests ──────────────────────────────────────────────────────────────
-- One row per test instance. `config` holds the full MockTestConfig json so the
-- result endpoint can reconstruct what was asked for without extra joins.
CREATE TABLE IF NOT EXISTS public.mock_tests (
  mock_test_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        text NOT NULL,
  exam           text NOT NULL DEFAULT 'JEE_ADVANCED'
                   CHECK (exam IN ('JEE_ADVANCED', 'JEE_MAIN', 'custom')),
  config         jsonb NOT NULL DEFAULT '{}'::jsonb,   -- full MockTestConfig
  status         text NOT NULL DEFAULT 'built'
                   CHECK (status IN ('built', 'in_progress', 'submitted', 'graded')),
  -- Feasibility metadata stored at build time
  total_questions     integer NOT NULL DEFAULT 0,
  questions_requested integer NOT NULL DEFAULT 0,
  -- Timing
  time_limit_minutes  integer NOT NULL DEFAULT 180,
  started_at          timestamptz,
  submitted_at        timestamptz,
  -- Aggregate scores filled in on submit
  raw_score           real,          -- sum of per-question scores from marking scheme
  max_score           real,          -- maximum possible score
  accuracy_pct        real,          -- 0–100
  -- Per-subject breakdown (jsonb for flexibility)
  subject_scores      jsonb NOT NULL DEFAULT '{}'::jsonb,
  chapter_scores      jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- LLM debrief (generated async after result is first fetched)
  debrief             text,
  debrief_generated_at timestamptz,
  -- Housekeeping
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mock_tests_user_idx
  ON public.mock_tests (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mock_tests_status_idx
  ON public.mock_tests (user_id, status);

COMMENT ON TABLE public.mock_tests IS
  'One row per mock test instance. Stores the full config, timing, and aggregate results.';
COMMENT ON COLUMN public.mock_tests.config IS
  'Full MockTestConfig as jsonb: subjects, chapters, exam, marking_scheme, time_limit, etc.';
COMMENT ON COLUMN public.mock_tests.status IS
  'built → in_progress (after /start) → submitted (after /submit) → graded (scores computed).';

-- ─── mock_test_questions ─────────────────────────────────────────────────────
-- One row per question assembled into a mock test. Stores the metadata needed
-- to display the question and grade the answer without re-querying the corpus.
CREATE TABLE IF NOT EXISTS public.mock_test_questions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mock_test_id   uuid NOT NULL REFERENCES public.mock_tests(mock_test_id) ON DELETE CASCADE,
  content_id     text NOT NULL,       -- FK to public.content (retrieval DB)
  display_order  integer NOT NULL,    -- 1-based order within the test
  section        text NOT NULL,       -- 'Physics' | 'Chemistry' | 'Mathematics'
  chapter_id     text NOT NULL,
  chapter_title  text,
  concept_id     text,
  concept_name   text,
  question_type  text NOT NULL,       -- single_correct | numeric | multiple_correct | integer | comprehension
  difficulty     integer,
  -- Snapshot of question fields at build time so answers can always be graded
  -- even if the retrieval DB row changes later.
  stem           text,
  options        jsonb,               -- [{label, text}, ...]  null for numeric
  answer         jsonb,               -- the official answer; stored here so grading
                                      -- does not require a cross-DB join at answer time
  has_gradekey   boolean NOT NULL DEFAULT false,  -- false → ungradable (subjective/matching)
  UNIQUE (mock_test_id, content_id)
);

CREATE INDEX IF NOT EXISTS mock_test_questions_test_idx
  ON public.mock_test_questions (mock_test_id, display_order);
CREATE INDEX IF NOT EXISTS mock_test_questions_section_idx
  ON public.mock_test_questions (mock_test_id, section);

COMMENT ON TABLE public.mock_test_questions IS
  'Questions assembled into a mock test with a snapshot of metadata needed for grading.';
COMMENT ON COLUMN public.mock_test_questions.answer IS
  'Snapshotted official answer (jsonb, same structure as knowledge_private.solutions.answer). '
  'Never exposed to the student until after submit.';
COMMENT ON COLUMN public.mock_test_questions.has_gradekey IS
  'True when a discrete answer key is available. False for subjective / matching questions.';

-- ─── mock_test_submissions ───────────────────────────────────────────────────
-- One row per student answer. One row may be absent (unattempted).
-- Grades are written at submit time, not when the answer is saved.
CREATE TABLE IF NOT EXISTS public.mock_test_submissions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mock_test_id     uuid NOT NULL REFERENCES public.mock_tests(mock_test_id) ON DELETE CASCADE,
  content_id       text NOT NULL,
  user_id          text NOT NULL,
  -- Student response
  student_answer   text,             -- raw text of the submission
  choice           jsonb,            -- ['A', 'C'] for MCQ; null for numeric
  numeric_value    real,             -- parsed numeric for integer/numeric types
  -- Timing
  answered_at      timestamptz NOT NULL DEFAULT now(),
  time_taken_sec   integer,          -- seconds on this question; null if not tracked
  -- Grading (filled on /submit)
  grade_status     text              -- correct | incorrect | ungradable | unattempted
                   CHECK (grade_status IS NULL OR grade_status IN
                     ('correct','incorrect','ungradable','unattempted','invalid_submission')),
  is_correct       boolean,
  question_score   real,             -- score for this question after applying marking scheme
  answer_text      text,             -- the correct answer (revealed after submit)
  graded_at        timestamptz,
  -- Attempt linkage (for commit_progress)
  attempt_id       text,             -- UUID from commit_graded_attempt RPC
  concept_id       text,
  UNIQUE (mock_test_id, content_id, user_id)
);

CREATE INDEX IF NOT EXISTS mock_submissions_test_idx
  ON public.mock_test_submissions (mock_test_id, user_id);
CREATE INDEX IF NOT EXISTS mock_submissions_user_idx
  ON public.mock_test_submissions (user_id, answered_at DESC);

COMMENT ON TABLE public.mock_test_submissions IS
  'One row per student answer in a mock test. Grades are computed on /submit, not when saved.';
COMMENT ON COLUMN public.mock_test_submissions.grade_status IS
  'Null until submit. Filled by apply_marking_scheme after the test is submitted.';
COMMENT ON COLUMN public.mock_test_submissions.question_score IS
  'Points earned for this question according to the exam marking scheme (may be negative).';

-- ─── Grants ──────────────────────────────────────────────────────────────────
ALTER TABLE public.mock_tests            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mock_test_questions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mock_test_submissions ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.mock_tests            TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.mock_test_questions   TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.mock_test_submissions TO service_role;

NOTIFY pgrst, 'reload schema';
