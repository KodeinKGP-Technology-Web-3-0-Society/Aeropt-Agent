-- Read-only: which agent migrations (006-018) exist on this database?
-- Paste into the Supabase dashboard -> SQL Editor and Run. Changes nothing.
SELECT migration, applied FROM (VALUES
  ('006_learner_memory: learner_memory',           to_regclass('public.learner_memory') IS NOT NULL),
  ('006_learner_memory: review_schedule',          to_regclass('public.review_schedule') IS NOT NULL),
  ('006_learner_memory: agent_trace',              to_regclass('public.agent_trace') IS NOT NULL),
  ('008_learner_trajectory_view',                  to_regclass('public.learner_trajectory') IS NOT NULL),
  ('009_learner_memory_embedding',                 EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'learner_memory' AND column_name = 'embedding')),
  ('010_study_room: study_sessions',               to_regclass('public.study_sessions') IS NOT NULL),
  ('010_study_room: study_session_events',         to_regclass('public.study_session_events') IS NOT NULL),
  ('011_study_lesson_plan',                        EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'study_sessions' AND column_name = 'lesson_plan')),
  ('012_learning_events',                          to_regclass('public.learning_events') IS NOT NULL),
  ('013_study_plan_settings',                      to_regclass('public.study_plan_settings') IS NOT NULL),
  ('014_study_session_mode',                       EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'study_sessions' AND column_name = 'question_source')),
  ('015_session_kind_title',                       EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'sessions' AND column_name = 'kind')),
  ('016_durable_agent_state: served_items',        to_regclass('public.served_items') IS NOT NULL),
  ('016_durable_agent_state: carry_sessions',      to_regclass('public.carry_sessions') IS NOT NULL),
  ('017_episodic_memory',                          to_regclass('public.episodic_memory') IS NOT NULL),
  ('018_episodic_memory_approach: approach',       EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'episodic_memory' AND column_name = 'approach')),
  ('018_episodic_memory_approach: consolidated_at', EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'episodic_memory' AND column_name = 'consolidated_at')),
  ('018_episodic_memory_approach: episodic_consolidation', to_regclass('public.episodic_consolidation') IS NOT NULL),
  ('020_agent_trace_metrics: metrics',             EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'agent_trace' AND column_name = 'metrics')),
  ('021_episodic_memory_evidence: assessment_status', EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'episodic_memory' AND column_name = 'assessment_status')),
  ('022_grading_commit: submission_id',            EXISTS (SELECT 1 FROM information_schema.columns
                                                     WHERE table_schema = 'public' AND table_name = 'user_attempts' AND column_name = 'submission_id')),
  ('022_grading_commit: commit_graded_attempt',    to_regprocedure('public.commit_graded_attempt(jsonb)') IS NOT NULL)
) AS t(migration, applied)
ORDER BY migration;
