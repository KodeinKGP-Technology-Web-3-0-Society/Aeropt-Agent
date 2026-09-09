-- Remove agent_memory table and structured_memory column from sessions since they are unused.
DROP TABLE IF EXISTS public.agent_memory;
ALTER TABLE public.sessions DROP COLUMN IF EXISTS structured_memory;
