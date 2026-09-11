-- 007_chat_turns_agent_used.sql
--
-- Widen chat_turns.agent_used to cover the autonomous Learning Agent.
--
-- The original check constraint enumerated the nine graph nodes. Those nodes are
-- gone: one Learning Agent now writes the reply (sometimes accepting a composer
-- expert's draft verbatim), and the deterministic graded fast path handles
-- button clicks. Writing the student's own message also needs a label that says
-- "the student said this" rather than borrowing whichever node happened to
-- record it — the old code labelled user turns "orchestrator", which made the
-- column useless for telling who actually produced a turn.
--
-- Until this is applied, db.insert_chat_turn maps new labels onto legacy ones
-- (see _AGENT_USED_COMPAT there) so the service runs against either schema.
-- Once it is applied, that map can be emptied.

ALTER TABLE public.chat_turns
  DROP CONSTRAINT IF EXISTS chat_turns_agent_used_check;

ALTER TABLE public.chat_turns
  ADD CONSTRAINT chat_turns_agent_used_check
  CHECK (
    agent_used IS NULL
    OR agent_used IN (
      -- current
      'student',            -- the learner's own message
      'learning_agent',     -- the autonomous tutor's reply
      'graded_fast_path',   -- deterministic Correct/Incorrect handling
      'answer_checker',     -- a reply the checker rewrote
      -- still in use: composers and deterministic helpers
      'solver',
      'socratic',
      'quiz',
      'practice_generator',
      'diagnostic',
      'follow_up',
      'context_manager',
      -- retired, kept so historical rows stay valid
      'orchestrator',
      'tutor',
      'syllabus',
      'doubt_resolver',
      'practice_coach',
      'mentor_briefing'
    )
  );

COMMENT ON COLUMN public.chat_turns.agent_used IS
  'Who produced this turn: the student, the Learning Agent, or a named deterministic step.';
