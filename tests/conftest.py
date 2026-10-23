"""Test-wide novelty-ledger isolation; other database clients must be mocked.

The novelty ledger writes through to `public.served_items` in the user-data Supabase.
Retrieval tests serve questions to made-up learners, so the ledger is kept in memory
unless a test opts in (tests/test_durable_state.py, against a fake client).
"""

import os

os.environ.setdefault("AEROPT_LEDGER", "memory")
