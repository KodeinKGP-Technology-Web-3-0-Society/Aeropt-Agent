-- 018_episodic_memory_approach.sql
-- Compact teaching approach, plus tracking for between-session consolidation.
--
-- Apply by hand after 017 (see APPLY.md). Safe to re-run. Does not rewrite
-- existing episode rows. The agent process does not run this file.

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS approach text;

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS consolidated_at timestamptz;

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS consolidation_memory_id uuid;

COMMENT ON COLUMN public.episodic_memory.approach IS
  'Short clip of the tutor reply for this turn. Not a transcript and not a model summary.';

CREATE INDEX IF NOT EXISTS episodic_memory_unconsolidated_idx
  ON public.episodic_memory (user_id, created_at DESC)
  WHERE status = 'active' AND consolidated_at IS NULL;

CREATE TABLE IF NOT EXISTS public.episodic_consolidation (
  user_id      text NOT NULL,
  cluster_key  text NOT NULL,
  episode_ids  jsonb NOT NULL,
  result       text NOT NULL,
  memory_id    uuid,
  fact         text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, cluster_key),
  CONSTRAINT episodic_consolidation_result_check
    CHECK (result IN ('saved', 'duplicate', 'skipped', 'rejected'))
);

ALTER TABLE public.episodic_consolidation ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON TABLE public.episodic_consolidation TO service_role;
