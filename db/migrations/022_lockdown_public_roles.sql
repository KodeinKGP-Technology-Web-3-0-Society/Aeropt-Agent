-- Lock the agent's tables away from Supabase's public API roles.
--
-- Every service here (agent, Django) talks to Supabase with the service_role key or a
-- direct Postgres login; the frontend never uses the anon key. So `anon` and
-- `authenticated` need no access to these tables at all. Several were left open:
-- the retrieval tables from 019 never enabled row level security, and 000 granted
-- TRUNCATE (which RLS does not gate) to both roles on every agent table.
--
-- Idempotent and data-preserving: it only turns RLS on and removes privileges.
-- service_role and postgres bypass RLS, so nothing the services do changes.
-- Skip any table that does not exist on this database.

DO $$
DECLARE
  t text;
  tables text[] := ARRAY[
    'agent_memory','agent_trace','carry_sessions','chapters','chat_turns','chunk_concepts',
    'concept_edges','concepts','content','content_gap_events','content_raw',
    'episodic_consolidation','episodic_memory','learner_memory','learner_readiness_scores',
    'learning_events','mastery_events','retrieval_meta','review_schedule','served_items',
    'sessions','study_plan_settings','study_session_events','study_sessions',
    'syllabus_topics','user_attempts','user_concept_state','user_learning_profile','users'
  ];
BEGIN
  FOREACH t IN ARRAY tables LOOP
    IF to_regclass(format('public.%I', t)) IS NOT NULL THEN
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated', t);
    END IF;
  END LOOP;
END $$;

-- Tables created later should not start open either.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
