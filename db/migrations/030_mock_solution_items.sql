-- Cache of worked solutions already checked for a mock.
-- The correct value is kept only when it was found in the solution text.
-- Rejected rows are remembered so the same solution is not sent to a model again.
-- Existing mock attempts are not changed.

CREATE TABLE IF NOT EXISTS public.mock_solution_items (
  content_id  text PRIMARY KEY,
  status      text NOT NULL CHECK (status IN ('accepted', 'rejected')),
  value       text,
  options     jsonb,
  answer      jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.mock_solution_items ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.mock_solution_items TO service_role;

COMMENT ON TABLE public.mock_solution_items IS
  'Printed final results copied from practice solutions and solved examples. A model may point at the text; the value is stored only if it appears there.';
