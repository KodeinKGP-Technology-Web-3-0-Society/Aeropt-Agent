-- Migration 005: Add root_cause_concept_id to user_attempts
ALTER TABLE public.user_attempts
    ADD COLUMN IF NOT EXISTS root_cause_concept_id uuid REFERENCES public.concepts(concept_id);
