-- 026_grading_commit.sql
-- One transactional, idempotent progress write for a graded submission.
--
-- Apply by hand after the agent schema through 021. Safe to re-run. It does not rewrite historical
-- proficiency. timed_attempts starts at 0: old averages keep their stored
-- value, and the next valid time starts a new measured series instead of
-- pretending every past attempt was timed.
--
-- The Python service calls public.commit_graded_attempt. It does not fall
-- back to separate inserts when this function is missing.
-- EXECUTE is limited to service_role. The function runs as the caller
-- (SECURITY INVOKER) with a fixed search_path.

ALTER TABLE public.user_attempts
  ADD COLUMN IF NOT EXISTS submission_id text,
  ADD COLUMN IF NOT EXISTS question_instance_id text,
  ADD COLUMN IF NOT EXISTS answer_fingerprint text,
  ADD COLUMN IF NOT EXISTS grade_status text,
  ADD COLUMN IF NOT EXISTS evidence_source text,
  ADD COLUMN IF NOT EXISTS grade_result jsonb;

ALTER TABLE public.user_concept_state
  ADD COLUMN IF NOT EXISTS timed_attempts integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.user_concept_state.timed_attempts IS
  'Count of attempts with a valid non-negative time. Not backfilled from attempts. 0 means the stored average has no known sample count.';

CREATE UNIQUE INDEX IF NOT EXISTS user_attempts_submission_uidx
  ON public.user_attempts (user_id, submission_id)
  WHERE submission_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS learning_events_submission_uidx
  ON public.learning_events (user_id, (payload->>'submission_id'))
  WHERE kind = 'attempt' AND payload ? 'submission_id';

CREATE UNIQUE INDEX IF NOT EXISTS study_events_submission_uidx
  ON public.study_session_events (study_session_id, (payload->>'submission_id'))
  WHERE event_type = 'question_attempted' AND payload ? 'submission_id';

ALTER TABLE public.user_attempts DROP CONSTRAINT IF EXISTS user_attempts_error_type_check;
ALTER TABLE public.user_attempts
  ADD CONSTRAINT user_attempts_error_type_check
  CHECK (
    error_type IS NULL
    OR error_type = ANY (ARRAY['conceptual','procedural','calculation','misread','unknown'])
  );

CREATE OR REPLACE FUNCTION public.commit_graded_attempt(p_request jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_user uuid;
  v_session uuid;
  v_study uuid;
  v_owner uuid;
  v_concept text;
  v_submission text;
  v_fingerprint text;
  v_correct boolean;
  v_status text;
  v_evidence text;
  v_existing public.user_attempts%ROWTYPE;
  v_cs public.user_concept_state%ROWTYPE;
  v_ss public.study_sessions%ROWTYPE;
  v_old double precision;
  v_new double precision;
  v_outcome double precision;
  v_alpha double precision;
  v_mult double precision;
  v_factor double precision;
  v_time integer;
  v_timed integer;
  v_avg double precision;
  v_new_avg double precision;
  v_new_timed integer;
  v_attempts integer;
  v_correct_n integer;
  v_streak integer;
  v_flagged boolean;
  v_weak double precision;
  v_clear double precision;
  v_consec integer;
  v_row record;
  v_error text;
  v_attempt_id uuid;
  v_was_cold boolean;
  v_ps integer;
  v_ws integer;
  v_diff integer;
  v_changed boolean;
  v_served integer;
  v_right integer;
  v_ev record;
  v_result jsonb;
BEGIN
  IF p_request IS NULL OR jsonb_typeof(p_request) <> 'object' THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'invalid_request');
  END IF;

  BEGIN
    v_user := (p_request->>'user_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'invalid_user');
  END;

  v_concept := btrim(COALESCE(p_request->>'concept_id', ''));
  v_submission := btrim(COALESCE(p_request->>'submission_id', ''));
  v_fingerprint := btrim(COALESCE(p_request->>'answer_fingerprint', ''));
  v_status := p_request->>'grade_status';
  v_evidence := p_request->>'evidence_source';
  IF v_concept = '' OR v_submission = '' OR v_fingerprint = '' THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'identity_unavailable');
  END IF;
  IF v_evidence = 'answer_key' AND v_status NOT IN ('correct', 'incorrect') THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'progress_rejected');
  END IF;
  IF v_evidence = 'student_self_report' AND v_status <> 'self_reported' THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'progress_rejected');
  END IF;
  IF v_evidence NOT IN ('answer_key', 'student_self_report') THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'progress_rejected');
  END IF;
  IF p_request->>'is_correct' IS NULL OR p_request->>'is_correct' NOT IN ('true', 'false') THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'progress_rejected');
  END IF;
  v_correct := (p_request->>'is_correct')::boolean;

  v_session := NULL;
  IF COALESCE(p_request->>'session_id', '') <> '' THEN
    BEGIN
      v_session := (p_request->>'session_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
      RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'session_not_owned');
    END;
    SELECT user_id INTO v_owner FROM public.sessions WHERE session_id = v_session;
    IF NOT FOUND OR v_owner IS DISTINCT FROM v_user THEN
      RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'session_not_owned');
    END IF;
  END IF;

  v_study := NULL;
  IF COALESCE(p_request->>'study_session_id', '') <> '' THEN
    BEGIN
      v_study := (p_request->>'study_session_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
      RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'study_session_not_owned');
    END;
  END IF;

  SELECT * INTO v_existing
  FROM public.user_attempts
  WHERE user_id = v_user AND submission_id = v_submission
  FOR UPDATE;
  IF FOUND THEN
    IF v_existing.answer_fingerprint IS DISTINCT FROM v_fingerprint
       OR (
         COALESCE(p_request->>'content_id', '') <> ''
         AND v_existing.content_id IS NOT NULL
         AND v_existing.content_id IS DISTINCT FROM p_request->>'content_id'
       )
       OR (
         COALESCE(p_request->>'question_instance_id', '') <> ''
         AND v_existing.question_instance_id IS NOT NULL
         AND v_existing.question_instance_id IS DISTINCT FROM p_request->>'question_instance_id'
       )
    THEN
      RETURN jsonb_build_object('status', 'conflict', 'failure_reason', 'submission_conflict');
    END IF;
    IF v_existing.grade_result IS NOT NULL THEN
      RETURN v_existing.grade_result || jsonb_build_object('status', 'replay');
    END IF;
    RETURN jsonb_build_object(
      'status', 'replay',
      'attempt_id', v_existing.attempt_id,
      'submission_id', v_existing.submission_id,
      'question_instance_id', v_existing.question_instance_id,
      'content_id', v_existing.content_id,
      'concept_id', v_existing.concept_id,
      'is_correct', v_existing.is_correct,
      'grade_status', v_existing.grade_status,
      'evidence_source', v_existing.evidence_source
    );
  END IF;

  SELECT * INTO v_cs
  FROM public.user_concept_state
  WHERE user_id = v_user AND concept_id = v_concept
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'no_concept_state');
  END IF;

  v_old := v_cs.proficiency_score;
  IF v_old IS NULL OR v_old <> v_old OR v_old < 0 OR v_old > 1 THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'invalid_proficiency');
  END IF;

  v_alpha := COALESCE((p_request->>'alpha')::double precision, 0.7);
  v_mult := COALESCE((p_request->>'dampen_multiplier')::double precision, 2.0);
  v_factor := COALESCE((p_request->>'dampen_factor')::double precision, 0.7);
  v_weak := COALESCE((p_request->>'weak_threshold')::double precision, 0.4);
  v_clear := COALESCE((p_request->>'clear_weak_threshold')::double precision, 0.65);
  v_time := NULL;
  IF p_request ? 'time_taken_sec' AND p_request->>'time_taken_sec' IS NOT NULL AND p_request->>'time_taken_sec' <> '' THEN
    v_time := (p_request->>'time_taken_sec')::integer;
    IF v_time < 0 THEN
      v_time := NULL;
    END IF;
  END IF;
  v_timed := COALESCE(v_cs.timed_attempts, 0);
  v_avg := COALESCE(v_cs.avg_time_seconds, 0);
  v_outcome := CASE WHEN v_correct THEN 1.0 ELSE 0.0 END;
  IF v_correct AND v_timed > 0 AND v_avg > 0 AND v_time IS NOT NULL AND v_time > v_mult * v_avg THEN
    v_outcome := v_outcome * v_factor;
  END IF;
  v_new := round((v_alpha * v_old + (1.0 - v_alpha) * v_outcome)::numeric, 4)::double precision;
  IF v_new < 0 OR v_new > 1 OR v_new <> v_new THEN
    RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'invalid_proficiency');
  END IF;

  IF v_time IS NULL THEN
    v_new_avg := v_avg;
    v_new_timed := v_timed;
  ELSIF v_timed <= 0 THEN
    v_new_avg := v_time;
    v_new_timed := 1;
  ELSE
    v_new_timed := v_timed + 1;
    v_new_avg := v_avg + (v_time - v_avg) / v_new_timed;
  END IF;

  v_attempts := COALESCE(v_cs.attempts, 0) + 1;
  v_correct_n := COALESCE(v_cs.correct, 0) + CASE WHEN v_correct THEN 1 ELSE 0 END;
  v_streak := CASE WHEN v_correct THEN 0 ELSE COALESCE(v_cs.struggle_streak, 0) + 1 END;
  v_flagged := COALESCE(v_cs.flagged_weak, false);
  v_was_cold := COALESCE(v_cs.is_cold_start, false) OR COALESCE(v_cs.attempts, 0) = 0;
  IF v_new < v_weak THEN
    v_flagged := true;
  ELSIF v_new > v_clear THEN
    v_consec := 0;
    FOR v_row IN
      SELECT is_correct
      FROM public.user_attempts
      WHERE user_id = v_user AND concept_id = v_concept
      ORDER BY created_at DESC NULLS LAST
      LIMIT 5
    LOOP
      IF v_row.is_correct IS TRUE THEN
        v_consec := v_consec + 1;
      ELSE
        EXIT;
      END IF;
    END LOOP;
    IF v_correct AND v_consec + 1 >= 2 THEN
      v_flagged := false;
    END IF;
  END IF;

  v_error := NULL;
  IF NOT v_correct THEN
    v_error := COALESCE(NULLIF(p_request->>'error_type', ''), 'unknown');
    IF v_error NOT IN ('conceptual', 'procedural', 'calculation', 'misread', 'unknown') THEN
      v_error := 'unknown';
    END IF;
  END IF;

  UPDATE public.user_concept_state
  SET proficiency_score = v_new,
      attempts = v_attempts,
      correct = v_correct_n,
      struggle_streak = v_streak,
      flagged_weak = v_flagged,
      is_cold_start = false,
      avg_time_seconds = round(v_new_avg::numeric, 2),
      timed_attempts = v_new_timed,
      error_type_dist = CASE
        WHEN v_error IS NULL OR v_error = 'unknown' THEN COALESCE(error_type_dist, '{}'::jsonb)
        ELSE jsonb_set(
          COALESCE(error_type_dist, '{}'::jsonb),
          ARRAY[v_error],
          to_jsonb(COALESCE((error_type_dist->>v_error)::integer, 0) + 1),
          true
        )
      END,
      updated_at = now(),
      last_seen_at = now()
  WHERE user_id = v_user AND concept_id = v_concept;

  INSERT INTO public.user_attempts (
    user_id, session_id, concept_id, content_id, is_correct, error_type,
    time_taken_sec, student_answer, submission_id, question_instance_id,
    answer_fingerprint, grade_status, evidence_source
  ) VALUES (
    v_user, v_session, v_concept, NULLIF(p_request->>'content_id', ''),
    v_correct, v_error, v_time, NULLIF(p_request->>'student_answer', ''),
    v_submission, NULLIF(p_request->>'question_instance_id', ''),
    v_fingerprint, v_status, v_evidence
  )
  RETURNING attempt_id INTO v_attempt_id;

  v_served := NULL;
  v_right := NULL;
  v_diff := NULL;
  IF v_study IS NOT NULL THEN
    SELECT * INTO v_ss FROM public.study_sessions WHERE study_session_id = v_study FOR UPDATE;
    IF NOT FOUND OR v_ss.user_id IS DISTINCT FROM (p_request->>'user_id') THEN
      RAISE EXCEPTION 'study_session_not_owned';
    END IF;
    v_ps := 0;
    v_ws := 0;
    FOR v_ev IN
      SELECT event_type, payload
      FROM public.study_session_events
      WHERE study_session_id = v_study
      ORDER BY created_at
    LOOP
      IF v_ev.event_type = 'question_attempted' THEN
        IF COALESCE((v_ev.payload->>'is_correct')::boolean, false) THEN
          v_ps := v_ps + 1;
          v_ws := 0;
        ELSE
          v_ps := 0;
          v_ws := v_ws + 1;
        END IF;
      ELSIF v_ev.event_type = 'difficulty_adjusted' THEN
        v_ps := 0;
        v_ws := 0;
      END IF;
    END LOOP;
    IF v_correct THEN
      v_ps := v_ps + 1;
      v_ws := 0;
    ELSE
      v_ps := 0;
      v_ws := v_ws + 1;
    END IF;
    v_diff := COALESCE(v_ss.difficulty_level, 1);
    v_changed := false;
    IF v_ps >= COALESCE((p_request->>'difficulty_up_streak')::integer, 3)
       AND v_diff < COALESCE((p_request->>'difficulty_max')::integer, 5) THEN
      v_diff := v_diff + 1;
      v_ps := 0;
      v_changed := true;
    ELSIF v_ws >= COALESCE((p_request->>'difficulty_down_streak')::integer, 2)
          AND v_diff > COALESCE((p_request->>'difficulty_min')::integer, 1) THEN
      v_diff := v_diff - 1;
      v_ws := 0;
      v_changed := true;
    END IF;
    INSERT INTO public.study_session_events (
      study_session_id, user_id, event_type, concept_id, content_id, payload
    ) VALUES (
      v_study, p_request->>'user_id', 'question_attempted', v_concept,
      NULLIF(p_request->>'content_id', ''),
      jsonb_build_object(
        'is_correct', v_correct,
        'student_answer', COALESCE(p_request->>'student_answer', ''),
        'evidence_source', v_evidence,
        'grade_status', v_status,
        'submission_id', v_submission,
        'difficulty', p_request->'question_difficulty'
      )
    );
    IF v_changed THEN
      INSERT INTO public.study_session_events (
        study_session_id, user_id, event_type, concept_id, payload
      ) VALUES (
        v_study, p_request->>'user_id', 'difficulty_adjusted', v_concept,
        jsonb_build_object('new_difficulty', v_diff, 'submission_id', v_submission)
      );
    END IF;
    UPDATE public.study_sessions
    SET questions_served = questions_served + 1,
        questions_correct = questions_correct + CASE WHEN v_correct THEN 1 ELSE 0 END,
        difficulty_level = v_diff
    WHERE study_session_id = v_study
    RETURNING questions_served, questions_correct INTO v_served, v_right;
  END IF;

  INSERT INTO public.learning_events (
    user_id, kind, source, concept_id, content_id, session_id, study_session_id,
    outcome, error_type, difficulty, time_taken_sec, proficiency_before,
    proficiency_after, payload
  ) VALUES (
    p_request->>'user_id',
    'attempt',
    COALESCE(NULLIF(p_request->>'source', ''), 'chat'),
    v_concept,
    NULLIF(p_request->>'content_id', ''),
    CASE WHEN v_session IS NULL THEN NULL ELSE v_session::text END,
    CASE WHEN v_study IS NULL THEN NULL ELSE v_study::text END,
    CASE WHEN v_correct THEN 'correct' ELSE 'wrong' END,
    v_error,
    CASE WHEN p_request->>'question_difficulty' IS NULL OR p_request->>'question_difficulty' = '' THEN NULL
         ELSE (p_request->>'question_difficulty')::integer END,
    v_time,
    v_old,
    v_new,
    jsonb_build_object(
      'submission_id', v_submission,
      'student_answer', left(COALESCE(p_request->>'student_answer', ''), 500),
      'evidence_source', v_evidence,
      'grade_status', v_status
    )
  );

  v_result := jsonb_build_object(
    'status', 'committed',
    'attempt_id', v_attempt_id,
    'submission_id', v_submission,
    'question_instance_id', NULLIF(p_request->>'question_instance_id', ''),
    'content_id', NULLIF(p_request->>'content_id', ''),
    'concept_id', v_concept,
    'is_correct', v_correct,
    'grade_status', v_status,
    'evidence_source', v_evidence,
    'proficiency_before', v_old,
    'proficiency_after', v_new,
    'answer_text', COALESCE(p_request->>'answer_text', ''),
    'was_cold_start', v_was_cold,
    'questions_served', v_served,
    'questions_correct', v_right,
    'difficulty_level', v_diff,
    'struggle_streak', v_streak
  );
  UPDATE public.user_attempts
  SET grade_result = v_result
  WHERE attempt_id = v_attempt_id;
  RETURN v_result;
EXCEPTION
  WHEN unique_violation THEN
    SELECT * INTO v_existing
    FROM public.user_attempts
    WHERE user_id = v_user AND submission_id = v_submission;
    IF FOUND AND v_existing.answer_fingerprint = v_fingerprint AND v_existing.grade_result IS NOT NULL THEN
      RETURN v_existing.grade_result || jsonb_build_object('status', 'replay');
    END IF;
    RETURN jsonb_build_object('status', 'conflict', 'failure_reason', 'submission_conflict');
  WHEN OTHERS THEN
    IF SQLERRM = 'study_session_not_owned' THEN
      RETURN jsonb_build_object('status', 'rejected', 'failure_reason', 'study_session_not_owned');
    END IF;
    RAISE;
END;
$$;

REVOKE ALL ON FUNCTION public.commit_graded_attempt(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.commit_graded_attempt(jsonb) FROM anon;
REVOKE ALL ON FUNCTION public.commit_graded_attempt(jsonb) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.commit_graded_attempt(jsonb) TO service_role;

COMMENT ON FUNCTION public.commit_graded_attempt(jsonb) IS
  'Lock the concept row, insert one attempt, update proficiency and the ledger, and return the original row on an identical replay. Service role only.';
