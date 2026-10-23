"""Test-wide novelty-ledger isolation; other database clients must be mocked.

The novelty ledger writes through to `public.served_items` in the user-data Supabase.
Retrieval tests serve questions to made-up learners, so the ledger is kept in memory
unless a test opts in (tests/test_durable_state.py, against a fake client).
"""

import os

os.environ.setdefault("AEROPT_LEDGER", "memory")

# ── Stub env vars so config.py loads without a real .env in CI / offline tests.
# These values are intentionally fake — no test may call real network services
# without explicit mocking of the database / API clients.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RETRIEVAL_DATABASE_URL", "postgresql://test:test@localhost:5432/test_retrieval")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
