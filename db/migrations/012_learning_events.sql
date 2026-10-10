-- 012_learning_events.sql
-- One append-only ledger of everything a student does, across every surface.
--
-- Before this, progress lived in pieces: user_concept_state kept only the
-- latest proficiency (no history, so no trend), user_attempts had no surface or
-- score change, chat questions left no trace of which concept was worked on, and
-- Django's analytics tables (concept_mastery, question_attempts, daily_activity,
-- streaks) were never fed. The /progress page read those empty tables.
--
-- Every graded attempt (whichever path graded it), every tutor turn and every
-- study-session milestone now writes one row here. services/progress_report.py
-- derives streaks, active time, trends, chapter mastery and the mistake notebook
-- from it.
--
--   kind        attempt | tutor_turn | study_started | study_completed | review_done
--   source      chat | study | practice | assess | onboarding | review
--   outcome     correct | wrong (attempts); mastered | needs_review | incomplete (sessions)
--   proficiency_before / proficiency_after   the concept score around an attempt

CREATE TABLE IF NOT EXISTS public.learning_events (
  event_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             text NOT NULL,
  kind                text NOT NULL,
  source              text NOT NULL DEFAULT 'chat',
  concept_id          text,
  content_id          text,
  session_id          text,
  study_session_id    text,
  outcome             text,
  error_type          text,
  difficulty          integer,
  time_taken_sec      integer,
  proficiency_before  double precision,
  proficiency_after   double precision,
  payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT learning_events_kind_check
    CHECK (kind IN ('attempt','tutor_turn','study_started','study_completed','review_done'))
);

CREATE INDEX IF NOT EXISTS learning_events_user_time_idx
  ON public.learning_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS learning_events_user_concept_idx
  ON public.learning_events (user_id, concept_id, created_at DESC);

GRANT SELECT, INSERT ON public.learning_events TO service_role;

COMMENT ON TABLE public.learning_events IS
  'Append-only learner activity ledger across chat, study room, practice and assessments.';

-- ─── Backfill from what already exists ──────────────────────────────────────
-- Idempotent: only runs for users with no ledger rows yet.

INSERT INTO public.learning_events
  (user_id, kind, source, concept_id, content_id, session_id, outcome, error_type,
   time_taken_sec, payload, created_at)
SELECT a.user_id::text, 'attempt', 'chat', a.concept_id, a.content_id, a.session_id::text,
       CASE WHEN a.is_correct THEN 'correct' ELSE 'wrong' END, a.error_type,
       a.time_taken_sec,
       jsonb_build_object('student_answer', a.student_answer, 'backfilled', true),
       a.created_at AT TIME ZONE 'UTC'
FROM public.user_attempts a
WHERE NOT EXISTS (SELECT 1 FROM public.learning_events e WHERE e.user_id = a.user_id::text);

INSERT INTO public.learning_events
  (user_id, kind, source, concept_id, session_id, study_session_id, payload, created_at)
SELECT s.user_id, 'study_started', 'study', s.concept_id, s.agent_session_id,
       s.study_session_id::text, jsonb_build_object('backfilled', true), s.started_at
FROM public.study_sessions s
WHERE NOT EXISTS (
  SELECT 1 FROM public.learning_events e
  WHERE e.user_id = s.user_id AND e.kind <> 'attempt' AND e.payload->>'backfilled' IS NULL
)
AND EXISTS (SELECT 1 FROM public.chat_turns t WHERE t.session_id::text = s.agent_session_id);

INSERT INTO public.learning_events
  (user_id, kind, source, concept_id, session_id, study_session_id, outcome, difficulty,
   payload, created_at)
SELECT s.user_id, 'study_completed', 'study', s.concept_id, s.agent_session_id,
       s.study_session_id::text, s.outcome, s.difficulty_level,
       jsonb_build_object('questions_served', s.questions_served,
                          'questions_correct', s.questions_correct, 'backfilled', true),
       s.completed_at
FROM public.study_sessions s
WHERE s.completed_at IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM public.learning_events e
    WHERE e.study_session_id = s.study_session_id::text AND e.kind = 'study_completed'
  );

-- Study-room attempts are recorded with the study session's agent session id.
UPDATE public.learning_events e
SET source = 'study', study_session_id = s.study_session_id::text
FROM public.study_sessions s
WHERE e.kind = 'attempt' AND e.source = 'chat' AND e.session_id = s.agent_session_id;

NOTIFY pgrst, 'reload schema';
