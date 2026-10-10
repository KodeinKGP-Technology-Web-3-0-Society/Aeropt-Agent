-- Migration 004: Create content_gap_events table to log when retrieval yields no high-confidence hits.
-- Added status and updated_at so it can be used and updated by agents in the future.
--
-- concept_id is text with no foreign key: concepts.concept_id is text, and with the
-- local retrieval backend concept ids come from the retrieval index, not public.concepts.
-- (The first version declared uuid REFERENCES concepts and never applied anywhere.)

CREATE TABLE IF NOT EXISTS public.content_gap_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES public.users(user_id),
    session_id uuid REFERENCES public.sessions(session_id),
    concept_id text,
    query_text text NOT NULL,
    top_score float,
    status text DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Trigger to update 'updated_at' automatically
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_content_gap_events_updated_at ON public.content_gap_events;
CREATE TRIGGER update_content_gap_events_updated_at
BEFORE UPDATE ON public.content_gap_events
FOR EACH ROW
EXECUTE PROCEDURE update_updated_at_column();

GRANT SELECT, INSERT, UPDATE ON TABLE public.content_gap_events TO service_role;
