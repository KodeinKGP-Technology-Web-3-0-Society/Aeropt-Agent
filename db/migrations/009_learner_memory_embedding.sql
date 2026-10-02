-- 009_learner_memory_embedding.sql
-- Add an embedding column to learner_memory to support semantic recall.
-- Using real[] instead of vector(N) because the dimension varies between
-- local testing (static model, 256d) and production (Gemini, 3072d).

ALTER TABLE public.learner_memory ADD COLUMN IF NOT EXISTS embedding real[];
