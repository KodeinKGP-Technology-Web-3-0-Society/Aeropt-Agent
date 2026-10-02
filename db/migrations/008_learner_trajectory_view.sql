-- Migration: Learner Trajectory View
-- Combines user_concept_state with the recent mastery_events to provide a 
-- single readable view for the agent's context recall.

CREATE OR REPLACE VIEW public.learner_trajectory AS
SELECT 
    ucs.user_id,
    ucs.concept_id,
    ucs.chapter,
    ucs.proficiency_score,
    ucs.attempts,
    ucs.correct,
    ucs.struggle_streak,
    ucs.is_cold_start,
    ucs.error_type_dist,
    ucs.updated_at AS state_updated_at,
    (
        SELECT jsonb_agg(me) 
        FROM (
            SELECT mastery_change, reason, created_at
            FROM public.mastery_events
            WHERE user_id = ucs.user_id AND concept_id = ucs.concept_id
            ORDER BY created_at DESC
            LIMIT 5
        ) me
    ) AS recent_mastery_events
FROM public.user_concept_state ucs;

GRANT SELECT ON public.learner_trajectory TO service_role;
