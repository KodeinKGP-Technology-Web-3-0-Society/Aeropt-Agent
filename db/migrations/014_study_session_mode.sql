-- 014_study_session_mode.sql
-- Practice-only study sessions.
--
-- A student who already knows a topic wants questions, not a lesson. A session
-- in mode 'practice' starts in the practice phase (no Learn / Examples), and
-- question_source 'pyq' restricts it to JEE past-year questions.

ALTER TABLE public.study_sessions
  ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'learn',
  ADD COLUMN IF NOT EXISTS question_source text NOT NULL DEFAULT 'any';

DO $$ BEGIN
  ALTER TABLE public.study_sessions ADD CONSTRAINT study_sessions_mode_check CHECK (mode IN ('learn', 'practice'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE public.study_sessions ADD CONSTRAINT study_sessions_source_check CHECK (question_source IN ('any', 'pyq'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

NOTIFY pgrst, 'reload schema';
