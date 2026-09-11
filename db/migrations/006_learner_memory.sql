-- 006_learner_memory.sql
-- Durable learner memory for the autonomous Learning Agent.
--
-- Replaces the retired free-text-only memory model (see 003_retire_structured_memory.sql).
-- The difference from that retired attempt: this is NOT a dump of session state.
-- Each row is ONE atomic, evidence-backed fact the agent chose to remember about
-- a learner, with provenance and a confidence it can decay. The agent decides what
-- goes in here; nothing is written automatically by the pipeline.

-- ─── learner_memory ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.learner_memory (
  memory_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       text NOT NULL,
  kind          text NOT NULL,          -- preference | misconception | strength | context | goal
  subject_key   text,                   -- concept_id / chapter this is about, when scoped
  fact          text NOT NULL,          -- one sentence, learner-scoped, no PII beyond what they told us
  evidence      text,                   -- why the agent believes it (turn, attempt, observation)
  confidence    real NOT NULL DEFAULT 0.6,
  source        text NOT NULL DEFAULT 'tutor',   -- tutor | coach | diagnostic
  session_id    text,
  hit_count     integer NOT NULL DEFAULT 0,      -- times recalled into a prompt
  superseded_by uuid REFERENCES public.learner_memory(memory_id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT learner_memory_kind_check
    CHECK (kind IN ('preference', 'misconception', 'strength', 'context', 'goal')),
  CONSTRAINT learner_memory_confidence_check
    CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

-- Recall path: active memories for a user, strongest first.
CREATE INDEX IF NOT EXISTS learner_memory_user_active_idx
  ON public.learner_memory (user_id, confidence DESC)
  WHERE superseded_by IS NULL;

-- Scoped recall: "what do we know about this concept for this learner".
CREATE INDEX IF NOT EXISTS learner_memory_user_subject_idx
  ON public.learner_memory (user_id, subject_key)
  WHERE superseded_by IS NULL;

COMMENT ON TABLE public.learner_memory IS
  'Atomic agent-authored facts about a learner. One fact per row, with evidence and confidence.';

-- ─── review_schedule ─────────────────────────────────────────────────────────
-- Spaced review the agent proposes. The Background Learning Coach reads due rows.
CREATE TABLE IF NOT EXISTS public.review_schedule (
  review_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      text NOT NULL,
  concept_id   text NOT NULL,
  due_at       timestamptz NOT NULL,
  reason       text,
  status       text NOT NULL DEFAULT 'pending',  -- pending | done | cancelled
  created_by   text NOT NULL DEFAULT 'tutor',
  session_id   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT review_schedule_status_check
    CHECK (status IN ('pending', 'done', 'cancelled'))
);

-- One open review per (user, concept): re-scheduling updates rather than piles up.
CREATE UNIQUE INDEX IF NOT EXISTS review_schedule_open_unique_idx
  ON public.review_schedule (user_id, concept_id)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS review_schedule_due_idx
  ON public.review_schedule (user_id, due_at)
  WHERE status = 'pending';

COMMENT ON TABLE public.review_schedule IS
  'Agent-proposed spaced review. Read by the Background Learning Coach when due.';

-- ─── agent_trace ─────────────────────────────────────────────────────────────
-- One row per agent turn: what the agent decided and why. This is the
-- observability replacement for reading conditional edges off a flowchart.
CREATE TABLE IF NOT EXISTS public.agent_trace (
  trace_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      text NOT NULL,
  session_id   text NOT NULL,
  turn_index   integer,
  route        text NOT NULL,            -- fast_path | agent_loop
  steps        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- ordered tool calls / handoffs
  tool_calls   integer NOT NULL DEFAULT 0,
  handoffs     integer NOT NULL DEFAULT 0,
  checker_verdict text,                  -- pass | revised | failed | skipped
  latency_ms   integer,
  model_used   text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agent_trace_session_idx
  ON public.agent_trace (session_id, created_at DESC);

COMMENT ON TABLE public.agent_trace IS
  'Per-turn decision trace for the autonomous Learning Agent: tools called, handoffs, checker verdict.';
