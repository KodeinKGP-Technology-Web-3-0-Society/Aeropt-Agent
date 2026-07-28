-- Allow onboarding preferred_depth values used by Django + agent.
-- Run in Supabase SQL editor if preferred_depth sync fails with users_preferred_depth_check.

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_preferred_depth_check;

ALTER TABLE users
  ADD CONSTRAINT users_preferred_depth_check
  CHECK (preferred_depth IS NULL OR preferred_depth IN ('quick', 'standard', 'deep'));
