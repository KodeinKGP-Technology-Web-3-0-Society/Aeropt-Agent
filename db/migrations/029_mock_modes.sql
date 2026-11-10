-- 029_mock_modes.sql
-- The personalized mock modes (smart, custom, official PYQ) do not add columns
-- and do not rewrite existing rows.
--
-- A paper's mode, blueprint, section instructions, and per-question marks live in
-- mock_tests.config. mock_test_questions keeps the same snapshot, including the
-- answer key, which the API still withholds until submit.
-- Completed attempts remain in mock_tests / mock_test_questions / mock_test_submissions.

COMMENT ON COLUMN public.mock_tests.config IS
  'Paper request plus, for papers built after the three-mode replacement, mock_type, blueprint, question_meta, and learner_fingerprint.';
