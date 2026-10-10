-- 016_durable_agent_state.sql
-- Supabase copies of state the agent otherwise keeps on local disk, for hosts
-- whose disk is wiped on restart (Hugging Face Spaces). Written and read by
-- services/durable_state.py when AEROPT_DURABLE_STATE=supabase; unused otherwise.
--
-- served_items   mirrors the retrieval novelty ledger (retrieval/store.py `served`),
--                so "don't repeat that question" survives a restart.
-- carry_sessions mirrors carry_store.py (quiz cursor, pending grading); 2-hour TTL
--                is applied on read, rows are deleted when a session ends.
-- Storage bucket `diagrams` holds compiled TikZ SVGs, private (served by the agent).
--
-- RLS is on with no policies: only the service role (which bypasses RLS) reads these.

CREATE TABLE IF NOT EXISTS public.served_items (
  user_id      text NOT NULL,
  content_id   text NOT NULL,
  concept_id   text,
  content_type text,
  purpose      text,
  fingerprint  text,
  served_at    double precision,
  times        integer NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, content_id)
);
CREATE INDEX IF NOT EXISTS served_items_user_concept_idx ON public.served_items (user_id, concept_id);

CREATE TABLE IF NOT EXISTS public.carry_sessions (
  session_id text PRIMARY KEY,
  carry      jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS carry_sessions_updated_idx ON public.carry_sessions (updated_at);

ALTER TABLE public.served_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.carry_sessions ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.served_items TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.carry_sessions TO service_role;

INSERT INTO storage.buckets (id, name, public)
VALUES ('diagrams', 'diagrams', false)
ON CONFLICT (id) DO NOTHING;

NOTIFY pgrst, 'reload schema';
