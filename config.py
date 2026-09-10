import os
import warnings
import certifi
from dotenv import load_dotenv

# Suppress pydantic v1 compatibility warning on Python 3.14.
# Comes from langchain_core internals — not our code, not an error.
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pydantic.v1.*", category=UserWarning)

load_dotenv()

# Fix SSL certificate verification for Neo4j Aura on Windows.
# Must be set before the neo4j driver is imported anywhere.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# ── Neo4j ─────────────────────────────────────────────────────────────────────
NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USER = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "physics-diagrams")

# ── LLM APIs ──────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# All Groq keys (GROQ_API_KEYS comma-list, or GROQ_API_KEY + GROQ_API_KEY_2..7).
# Consumed by utils/groq_pool.get_pool() so groq_complete() can rotate off a
# rate-limited key instead of only falling back to a weaker model. Previously
# this constant didn't exist at all — get_pool() referenced it but nothing ever
# called get_pool(), so the whole pool module was dead code (see utils/llm.py).
from utils.groq_pool import load_groq_api_keys as _load_groq_api_keys  # noqa: E402

GROQ_API_KEYS = _load_groq_api_keys()

# ── Model assignments per agent ───────────────────────────────────────────────
# Verified against this account's live Groq catalog on 2026-09-11
# (`client.models.list()`): llama-3.1-8b-instant and llama-3.3-70b-versatile —
# both used below until this fix — are decommissioned and 404 on every call.
# The only confirmed-live chat models on this account right now are
# qwen/qwen3.6-27b, qwen/qwen3.8-27b, openai/gpt-oss-120b, openai/gpt-oss-20b.
# Re-run that same models.list() check before relying on any of the four names
# below again — Groq's catalog on this account has now drifted three times.
#
# Orchestrator + lightweight classifiers → smallest fast model
ORCHESTRATOR_MODEL = "openai/gpt-oss-20b"
CONCEPT_RESOLVER_MODEL = "openai/gpt-oss-20b"
DIAGNOSTIC_MODEL = "openai/gpt-oss-20b"
CONTEXT_MANAGER_MODEL = "openai/gpt-oss-20b"

# Generation agents → larger reasoning models
# qwen/qwen3-32b was removed from Groq (Jul 2026); qwen3.6-27b is the replacement.
SOLVER_MODEL = "qwen/qwen3.6-27b"
SOCRATIC_MODEL = "openai/gpt-oss-120b"
QUIZ_MODEL = "qwen/qwen3.6-27b"

# ── Fallback chains (used when primary model is over capacity / unavailable) ──
# Listed in preference order — first available wins. Every entry below is
# confirmed live (see note above); none of these repeat the model it's a
# fallback for.
SOLVER_FALLBACK_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
]
QUIZ_FALLBACK_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
]
SOCRATIC_FALLBACK_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]
# Lightweight agents share a simpler fallback
SMALL_MODEL_FALLBACK = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
]

# Embedding (GraphRAG Retriever only) — no chat LLM
EMBEDDING_MODEL = "gemini-embedding-2"

# ── Neo4j vector index ────────────────────────────────────────────────────────
NEO4J_VECTOR_INDEX = "content_vector_index"
NEO4J_VECTOR_TOP_K = 5
NEO4J_PREREQ_DEPTH = 3
# Must be >= total Content nodes (currently 639). Used as fetch_k when
# candidate_ids filtering is active so no candidate is missed.
NEO4J_CONTENT_CORPUS_SIZE = 700

# ── Retrieval Confidence ──────────────────────────────────────────────────────
# well-matched queries scored 0.7+, off-syllabus queries scored below 0.45 in calibration on 2026-09-09
RETRIEVAL_CONFIDENCE_THRESHOLD = 0.55
RETRIEVAL_CONFIDENCE_MIN_HITS = 1
RETRIEVAL_TIMEOUT_MS = 600

# ── Diagnostic thresholds ─────────────────────────────────────────────────────
WEAK_THRESHOLD = 0.4
CLEAR_WEAK_THRESHOLD = 0.65  # clears flagged_weak; also lower bound for "almost mastered" UI band
MASTERY_THRESHOLD = 0.8      # meeting Jul 17 — concept mastery / roadmap skip gate (was 0.75 in roadmap)
PROFICIENCY_WMA_ALPHA = 0.7       # weight on old score in weighted moving average
TIME_DAMPEN_MULTIPLIER = 2.0      # if time_taken > X * avg_time, dampen proficiency gain
TIME_DAMPEN_FACTOR = 0.7          # multiply the outcome contribution by this

# ── Socratic routing thresholds ───────────────────────────────────────────────
SOCRATIC_PREREQ_MIN = 0.7
SOCRATIC_TARGET_MAX = 0.4
SOCRATIC_MAX_TURNS = 3            # auto-escalate to Solver after this many Socratic turns

# ── Quiz trigger ──────────────────────────────────────────────────────────────
STRUGGLE_STREAK_QUIZ_TRIGGER = 3

# ── Context Manager ───────────────────────────────────────────────────────────
CONTEXT_MANAGER_EVERY_N = 5      # summarise every N interactions
