-- 015_session_kind_title.sql
-- General tutor chat history.
--
-- `kind` separates the general chat page's conversations ('chat') from the
-- sessions Study Room, lesson and doubt rooms open (NULL), so the chat history
-- lists only the student's own chats. `title` is the name the student gave a
-- chat; when NULL the list shows the first question instead. `deleted_at` is a
-- soft delete: the service role has no DELETE grant on sessions, and practice
-- records (user_attempts) point at sessions, so a deleted chat is hidden, not removed.

ALTER TABLE public.sessions
  ADD COLUMN IF NOT EXISTS kind text,
  ADD COLUMN IF NOT EXISTS title text,
  ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

CREATE INDEX IF NOT EXISTS sessions_user_kind_updated_idx
  ON public.sessions (user_id, kind, updated_at DESC);

NOTIFY pgrst, 'reload schema';
