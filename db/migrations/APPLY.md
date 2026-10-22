# Applying agent migrations to the hosted (live) Supabase database

Local tooling never writes hosted data (`local_ingest/guard.py`), so hosted migrations are
applied by hand in the Supabase dashboard: **Project → SQL Editor → New query**.

1. **Check what is there** (read-only): paste `check_status.sql`, Run. Every row should say `true`.
2. **Apply each missing migration, in number order**, by pasting the whole file and Run:

   | Migration | Creates | Safe to re-run |
   |---|---|---|
   | `006_learner_memory.sql` | tables `learner_memory`, `review_schedule`, `agent_trace` (+ indexes, grants) | yes, `IF NOT EXISTS` throughout |
   | `009_learner_memory_embedding.sql` | column `learner_memory.embedding real[]` | yes |
   | `010_study_room.sql` … `014_study_session_mode.sql` | study-room tables and columns | yes |
   | `015_session_kind_title.sql` | columns `sessions.kind`, `sessions.title`, `sessions.deleted_at` (general chat history) | yes |
   | `016_durable_agent_state.sql` | tables `served_items`, `carry_sessions`, storage bucket `diagrams` (needed when `AEROPT_DURABLE_STATE=supabase`) | yes |
   | `017_episodic_memory.sql` | table `episodic_memory` | yes, `IF NOT EXISTS`; no data rewrite |
   | `018_episodic_memory_approach.sql` | column `episodic_memory.approach`, consolidation tracking columns, table `episodic_consolidation` | yes, `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`; no data rewrite |
   | `020_agent_trace_metrics.sql` | column `agent_trace.metrics jsonb` | yes, `IF NOT EXISTS` |
   | `021_episodic_memory_evidence.sql` | assessment provenance columns; consolidation result `claimed` | yes; does not rewrite old outcomes |
   | `022_lockdown_public_roles.sql` | enables RLS and revokes `anon`/`authenticated` on every agent table | yes; no data change. Run `check_exposure.sql` before and after |
   | `026_grading_commit.sql` | attempt idempotency columns, `timed_attempts`, and `commit_graded_attempt` | yes; does not rewrite historical scores. Required before progress writes succeed |

   Apply **006 before 009** (009 adds a column to 006's table). Apply **017 before 018**. Apply **020 before 021**. Apply **026 after 021, 012 and 010** (it writes `learning_events` and study tables). Numbers 023–025 belong to the retrieval database; they are applied by `local_ingest/push_index.py`, not to the agent database. The grading migration was numbered 026 to avoid colliding with GitHub's existing 022–025.

   Until 026 is applied, grading can still check an answer, but the service returns `persistence_status: failed` and `failure_reason: persistence_unavailable`. It does not fall back to separate attempt and proficiency writes.

   Rollback of 026: stop the service, `DROP FUNCTION public.commit_graded_attempt(jsonb);`, then drop the unique indexes and added columns only if no new rows depend on them. Do not drop columns that already store live submission ids without a backup. Replaying a submission id after a restore returns the stored `grade_result` when that jsonb is still present.
3. **Skip `008_learner_trajectory_view.sql`.** It references a column (`mastery_change`) that
   does not exist and fails even locally; nothing in the code reads the view.
4. Run `check_status.sql` again; then the memory and review tools stop returning `saved: false`.

None of these drop or change existing data.
