-- Phase 1 (Study Session Fix Plan): durable turn presentation
-- Apply in Supabase SQL editor (or psql) before deploying agent code that writes `presentation`.
--
-- Stores slides / flashcards / grading_ui / structured lesson blobs that previously
-- lived only in file-backed carry (.carry_sessions/, 2h TTL).

ALTER TABLE public.chat_turns
  ADD COLUMN IF NOT EXISTS presentation jsonb;

COMMENT ON COLUMN public.chat_turns.presentation IS
  'Assistant-turn UI blob: slides, flashcards, grading_ui, quiz_assessment, card, structured, etc. Null for user turns.';

-- Prefer unique (session_id, turn_index). Skip if duplicates already exist —
-- clean duplicates first, then uncomment:
-- CREATE UNIQUE INDEX IF NOT EXISTS chat_turns_session_turn_uidx
--   ON public.chat_turns (session_id, turn_index);
