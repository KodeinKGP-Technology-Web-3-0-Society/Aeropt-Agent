-- Migration 020: add metrics JSONB column to public.agent_trace (idempotent)
-- Stores telemetry: llm_calls, input_tokens, output_tokens, cached_tokens,
-- stop_reason, prompt_version, toolset_version, max_step_input_chars.

ALTER TABLE public.agent_trace
  ADD COLUMN IF NOT EXISTS metrics jsonb DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.agent_trace.metrics IS
  'Per-turn telemetry: llm_calls, input_tokens, output_tokens, cached_tokens, stop_reason, prompt_version, toolset_version, max_step_input_chars';
