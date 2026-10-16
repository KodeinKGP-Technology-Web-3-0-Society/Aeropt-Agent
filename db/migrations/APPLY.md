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

   Apply **006 before 009** (009 adds a column to 006's table).
3. **Skip `008_learner_trajectory_view.sql`.** It references a column (`mastery_change`) that
   does not exist and fails even locally; nothing in the code reads the view.
4. Run `check_status.sql` again; then the memory and review tools stop returning `saved: false`.

None of these drop or change existing data.
