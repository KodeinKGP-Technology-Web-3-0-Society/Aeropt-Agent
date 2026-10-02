-- 013_study_plan_settings.sql
-- Plan settings that the onboarding profile does not hold.
--
-- Exam, target year and daily minutes live in Django's student profile (set at
-- onboarding, edited from the Plan page via PATCH users/student-profiles/me/),
-- and the browser passes them to GET /api/v1/plan. These three are the plan's
-- own, kept here so they follow the student across devices:
--   study_days      days a week the student studies (rest days are planned)
--   revision_weeks  weeks before the exam kept free for revision and mocks
--   exam_date       the exact exam date, once NTA publishes it (else estimated)

CREATE TABLE IF NOT EXISTS public.study_plan_settings (
  user_id         text PRIMARY KEY,
  study_days      integer NOT NULL DEFAULT 6 CHECK (study_days BETWEEN 1 AND 7),
  revision_weeks  integer NOT NULL DEFAULT 6 CHECK (revision_weeks BETWEEN 0 AND 26),
  exam_date       date,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE ON public.study_plan_settings TO service_role;
NOTIFY pgrst, 'reload schema';
