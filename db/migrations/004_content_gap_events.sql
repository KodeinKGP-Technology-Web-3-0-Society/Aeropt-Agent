-- Migration 004: Create content_gap_events table to log when retrieval yields no high-confidence hits.
-- Added status and updated_at so it can be used and updated by agents in the future.

CREATE TABLE IF NOT EXISTS public.content_gap_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES public.users(user_id),
    session_id uuid REFERENCES public.sessions(session_id),
    concept_id uuid REFERENCES public.concepts(concept_id),
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

CREATE TRIGGER update_content_gap_events_updated_at
BEFORE UPDATE ON public.content_gap_events
FOR EACH ROW
EXECUTE PROCEDURE update_updated_at_column();
