-- 011_study_lesson_plan.sql
-- Study Room pacing: a visible lesson outline and per-phase progress.
--
-- Phase changes used to be left entirely to the model, which kept asking one
-- more check question instead of moving on ("I already know this — move on" got
-- another question). The teacher now commits to an outline for each phase and
-- ticks parts off; the server counts student turns in the current phase so it can
-- force the move when the model dawdles, and the UI shows "Part 2 of 3".
--
--   lesson_plan  {"learn": ["Energy storage", "The equation", "Frequency"],
--                 "examples": ["Find ω for given L, C", "Equal energy sharing"]}
--   phase_step   parts of the current phase already completed
--   phase_turns  student messages since the current phase started
--   evaluation_summary  the results-card text, so a finished session reopened
--                       from history still shows it

ALTER TABLE public.study_sessions
  ADD COLUMN IF NOT EXISTS lesson_plan jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS phase_step  integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS phase_turns integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS evaluation_summary text;

NOTIFY pgrst, 'reload schema';
