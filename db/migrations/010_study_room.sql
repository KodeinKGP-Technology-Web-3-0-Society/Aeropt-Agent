-- 010_study_room.sql
-- Study Room: session tracking, event log, and readiness scores.
--
-- Three tables that power the Study Room's sequential learning flow
-- (Learn → Examples → Practice → Evaluation). The teacher agent writes
-- session state; the evaluation agent reads events and writes readiness.

-- ─── study_sessions ──────────────────────────────────────────────────────────
-- One row per Study Room session. Tracks the concept being studied,
-- what phase the student is in, and the overall session outcome.
CREATE TABLE IF NOT EXISTS public.study_sessions (
  study_session_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           text NOT NULL,
  concept_id        text NOT NULL,
  concept_name      text,
  chapter           text,
  agent_session_id  text,                   -- links to the agent chat session
  current_phase     text NOT NULL DEFAULT 'learn',
  phase_history     jsonb NOT NULL DEFAULT '[]'::jsonb,
  questions_served  integer NOT NULL DEFAULT 0,
  questions_correct integer NOT NULL DEFAULT 0,
  difficulty_level  integer NOT NULL DEFAULT 1,
  started_at        timestamptz NOT NULL DEFAULT now(),
  completed_at      timestamptz,
  outcome           text,
  CONSTRAINT study_sessions_phase_check
    CHECK (current_phase IN ('learn','examples','practice','evaluation','completed')),
  CONSTRAINT study_sessions_outcome_check
    CHECK (outcome IS NULL OR outcome IN ('mastered','needs_review','incomplete'))
);

CREATE INDEX IF NOT EXISTS study_sessions_user_idx
  ON public.study_sessions (user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS study_sessions_concept_idx
  ON public.study_sessions (user_id, concept_id);

COMMENT ON TABLE public.study_sessions IS
  'One row per Study Room session. Tracks concept, phase, difficulty, and outcome.';

-- ─── study_session_events ────────────────────────────────────────────────────
-- Every meaningful thing that happened in a study session.
CREATE TABLE IF NOT EXISTS public.study_session_events (
  event_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  study_session_id  uuid NOT NULL REFERENCES public.study_sessions(study_session_id),
  user_id           text NOT NULL,
  event_type        text NOT NULL,
  concept_id        text,
  content_id        text,
  payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT study_events_type_check
    CHECK (event_type IN (
      'phase_start','phase_end','concept_taught','example_shown',
      'question_served','question_attempted','doubt_asked','doubt_resolved',
      'difficulty_adjusted','review_scheduled','session_completed'
    ))
);

CREATE INDEX IF NOT EXISTS study_events_session_idx
  ON public.study_session_events (study_session_id, created_at);
CREATE INDEX IF NOT EXISTS study_events_user_concept_idx
  ON public.study_session_events (user_id, concept_id, event_type);

COMMENT ON TABLE public.study_session_events IS
  'Granular event log for Study Room sessions. Every concept taught, question attempted, and doubt resolved.';

-- ─── learner_readiness_scores ────────────────────────────────────────────────
-- Predicted readiness per concept and overall. Updated after every
-- study session completion by the Evaluation Agent.
CREATE TABLE IF NOT EXISTS public.learner_readiness_scores (
  score_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           text NOT NULL,
  concept_id        text,
  score             real NOT NULL,
  confidence        real NOT NULL DEFAULT 0.5,
  factors           jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT readiness_score_range CHECK (score >= 0.0 AND score <= 100.0),
  CONSTRAINT readiness_confidence_range CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS readiness_scores_unique_idx
  ON public.learner_readiness_scores (user_id, COALESCE(concept_id, '__overall__'));

COMMENT ON TABLE public.learner_readiness_scores IS
  'Per-concept and overall exam readiness. Updated by the Evaluation Agent after each study session.';

-- Grants
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.study_sessions TO service_role;
GRANT SELECT, INSERT ON TABLE public.study_session_events TO service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.learner_readiness_scores TO service_role;
