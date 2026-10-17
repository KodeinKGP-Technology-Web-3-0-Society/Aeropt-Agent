-- 021_episodic_memory_evidence.sql
-- Provenance for an episode. Safe to re-run after 017 and 018.
-- Does not invent outcomes for rows already stored.
-- Apply by hand. The agent does not run this file.

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS assessment_status text;

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS evidence_source text;

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS student_response text;

ALTER TABLE public.episodic_memory
  ADD COLUMN IF NOT EXISTS interaction_id text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'episodic_memory_assessment_status_check'
  ) THEN
    ALTER TABLE public.episodic_memory
      ADD CONSTRAINT episodic_memory_assessment_status_check
      CHECK (
        assessment_status IS NULL OR assessment_status IN (
          'not_requested', 'pending', 'completed', 'failed', 'persistence_failed'
        )
      );
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'episodic_memory_evidence_source_check'
  ) THEN
    ALTER TABLE public.episodic_memory
      ADD CONSTRAINT episodic_memory_evidence_source_check
      CHECK (
        evidence_source IS NULL OR evidence_source IN (
          'none', 'answer_key', 'checker', 'model', 'student_self_report', 'dialogue'
        )
      );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS episodic_memory_interaction_idx
  ON public.episodic_memory (user_id, interaction_id, created_at DESC)
  WHERE status = 'active' AND interaction_id IS NOT NULL;

-- Allow the consolidation job to claim a cluster before writing a fact.
ALTER TABLE public.episodic_consolidation
  DROP CONSTRAINT IF EXISTS episodic_consolidation_result_check;

ALTER TABLE public.episodic_consolidation
  ADD CONSTRAINT episodic_consolidation_result_check
  CHECK (result IN ('saved', 'duplicate', 'skipped', 'rejected', 'claimed'));
