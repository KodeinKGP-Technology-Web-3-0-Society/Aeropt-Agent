"""Test-wide safety: test learners never reach the user database.

The novelty ledger writes through to `public.served_items` in the user-data Supabase.
Retrieval tests serve questions to made-up learners, so the ledger is kept in memory
unless a test opts in (tests/test_durable_state.py, against a fake client).
"""

import os

os.environ.setdefault("AEROPT_LEDGER", "memory")

# Memory jobs (memory/jobs.py) write the user database from background threads.
# Off for the suite; memory tests switch it on against an in-memory fake store.
os.environ.setdefault("MEMORY_MODE", "off")
