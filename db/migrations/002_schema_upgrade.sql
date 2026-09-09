-- =============================================================================
-- Aeropt Schema Upgrade — Steps 1-5
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- All statements are idempotent (safe to re-run).
-- =============================================================================

-- ─── Step 1: Add columns to user_concept_state ──────────────────────────────

ALTER TABLE public.user_concept_state
  ADD COLUMN IF NOT EXISTS consecutive_correct integer DEFAULT 0,
  ADD COLUMN IF NOT EXISTS content_seen text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS last_attempted_at timestamp with time zone,
  ADD COLUMN IF NOT EXISTS decay_applied boolean DEFAULT false;

-- ─── Step 2: Create user_learning_profile table ─────────────────────────────

CREATE TABLE IF NOT EXISTS public.user_learning_profile (
  user_id              uuid PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
  learning_velocity    double precision DEFAULT 0,
  preferred_content_type text DEFAULT 'balanced'
    CHECK (preferred_content_type IN ('theory_first', 'practice_first', 'balanced')),
  weak_chapters        text[] DEFAULT '{}',
  strong_chapters      text[] DEFAULT '{}',
  total_study_minutes  integer DEFAULT 0,
  avg_session_minutes  double precision DEFAULT 0,
  error_dominant_type  text,
  streak_days          integer DEFAULT 0,
  last_study_date      date,
  created_at           timestamp with time zone DEFAULT now(),
  updated_at           timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE public.user_learning_profile IS 'Global learning stats per user — consumed by Planner agent';

-- ─── Step 3: Create mastery_events table ────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.mastery_events (
  event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  concept_id  text NOT NULL,
  old_score   double precision,
  new_score   double precision,
  event_type  text NOT NULL CHECK (event_type IN ('mastered', 'unmastered', 'weak_flagged', 'weak_cleared')),
  created_at  timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mastery_events_user
  ON public.mastery_events (user_id, created_at DESC);

COMMENT ON TABLE public.mastery_events IS 'Score transition log — Django event consumer listens for roadmap unlocks';

-- ─── Step 4: Create agent_memory table ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.agent_memory (
  memory_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  memory_type  text NOT NULL CHECK (memory_type IN ('session', 'long_term', 'preference', 'flashcard')),
  content      jsonb NOT NULL DEFAULT '{}',
  relevance    double precision DEFAULT 1.0,
  source_session_id uuid,
  created_at   timestamp with time zone DEFAULT now(),
  expires_at   timestamp with time zone
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_user_type
  ON public.agent_memory (user_id, memory_type, created_at DESC);

COMMENT ON TABLE public.agent_memory IS 'Structured agent memory — replaces text blob summaries';

-- ─── Step 5: Add structured_memory to sessions ─────────────────────────────

ALTER TABLE public.sessions
  ADD COLUMN IF NOT EXISTS structured_memory jsonb;

COMMENT ON COLUMN public.sessions.structured_memory IS 'Structured JSON session memory (replaces text session_summary)';

-- ─── Verify ─────────────────────────────────────────────────────────────────
-- Run these SELECTs to confirm everything was created:

SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'user_concept_state' AND column_name IN ('consecutive_correct', 'content_seen', 'last_attempted_at', 'decay_applied');

SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN ('user_learning_profile', 'mastery_events', 'agent_memory');
