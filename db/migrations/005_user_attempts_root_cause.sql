-- Migration 005: Add root_cause_concept_id to user_attempts
-- text with no foreign key, matching concepts.concept_id (text); the first version
-- declared uuid REFERENCES concepts and never applied, so agents/diagnostic.py's
-- writes of this column failed.
ALTER TABLE public.user_attempts
    ADD COLUMN IF NOT EXISTS root_cause_concept_id text;
