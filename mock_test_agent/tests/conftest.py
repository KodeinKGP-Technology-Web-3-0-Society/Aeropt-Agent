"""Env stubs for mock-test tests only. The shared tests/conftest.py is untouched."""

import os

os.environ.setdefault("AEROPT_LEDGER", "memory")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RETRIEVAL_DATABASE_URL", "postgresql://test:test@localhost:5432/test_retrieval")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
