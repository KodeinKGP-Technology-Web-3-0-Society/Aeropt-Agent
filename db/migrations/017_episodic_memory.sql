-- 017_episodic_memory.sql
-- One compact episode per successful assistant turn.
--
-- Projection only: pointers and observed fields, not a copy of chat_turns,
-- agent_trace, or learner_memory. No foreign keys — existing id columns
-- disagree on uuid vs text. Invalidation is an UPDATE of status. The service
-- role has no DELETE grant.
--
-- Apply by hand (see APPLY.md). The agent process does not create this table.

CREATE TABLE IF NOT EXISTS public.episodic_memory (
  episode_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               text NOT NULL,
  session_id            text NOT NULL,
  study_session_id      text,
  assistant_turn_index  integer NOT NULL,

  surface               text NOT NULL,
  route                 text NOT NULL,
  task_type             text NOT NULL,

  content_id            text,
  concept_id            text,
  attempt_id            uuid,

  query_excerpt         text,
  actions               jsonb NOT NULL DEFAULT '[]'::jsonb,
  expert_used           text,
  checker_verdict       text,

  observed_outcome      text NOT NULL DEFAULT 'unknown',
  proficiency_before    double precision,
  proficiency_after     double precision,

  feedback_status       text NOT NULL DEFAULT 'pending',
  feedback_outcome      text,
  feedback_attempt_id   uuid,
  feedback_at           timestamptz,

  retrieval_text        text NOT NULL,
  embedding             real[],
  embed_model           text,

  status                text NOT NULL DEFAULT 'active',
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT episodic_memory_turn_unique UNIQUE (session_id, assistant_turn_index),
  CONSTRAINT episodic_memory_surface_check
    CHECK (surface IN ('chat', 'graded', 'study', 'lesson', 'doubt', 'assess')),
  CONSTRAINT episodic_memory_route_check
    CHECK (route IN ('agent_loop', 'graded_fast_path', 'teacher')),
  CONSTRAINT episodic_memory_task_check
    CHECK (task_type IN ('doubt', 'explain', 'practice', 'grade', 'study_phase')),
  CONSTRAINT episodic_memory_outcome_check
    CHECK (observed_outcome IN ('unknown', 'correct', 'incorrect', 'taught')),
  CONSTRAINT episodic_memory_feedback_status_check
    CHECK (feedback_status IN ('pending', 'next_attempt', 'none')),
  CONSTRAINT episodic_memory_feedback_outcome_check
    CHECK (feedback_outcome IS NULL OR feedback_outcome IN ('correct', 'incorrect')),
  CONSTRAINT episodic_memory_status_check
    CHECK (status IN ('active', 'invalid'))
);

CREATE INDEX IF NOT EXISTS episodic_memory_user_recent_idx
  ON public.episodic_memory (user_id, created_at DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS episodic_memory_user_concept_idx
  ON public.episodic_memory (user_id, concept_id, created_at DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS episodic_memory_pending_idx
  ON public.episodic_memory (user_id, concept_id, created_at DESC)
  WHERE status = 'active' AND feedback_status = 'pending';

ALTER TABLE public.episodic_memory ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON TABLE public.episodic_memory TO service_role;
