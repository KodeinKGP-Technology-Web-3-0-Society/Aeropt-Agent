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

# ── Model assignments per agent ───────────────────────────────────────────────
# Orchestrator + lightweight classifiers → smallest fast model
ORCHESTRATOR_MODEL = "llama-3.1-8b-instant"
CONCEPT_RESOLVER_MODEL = "llama-3.1-8b-instant"
DIAGNOSTIC_MODEL = "llama-3.1-8b-instant"
CONTEXT_MANAGER_MODEL = "llama-3.1-8b-instant"

# Generation agents → larger reasoning models
SOLVER_MODEL = "qwen/qwen3-32b"
SOCRATIC_MODEL = "llama-3.3-70b-versatile"
QUIZ_MODEL = "qwen/qwen3-32b"

# ── Fallback chains (used when primary model is over capacity / unavailable) ──
# Listed in preference order — first available wins.
SOLVER_FALLBACK_MODELS = [
    "qwen/qwen3.6-27b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]
QUIZ_FALLBACK_MODELS = [
    "qwen/qwen3.6-27b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]
SOCRATIC_FALLBACK_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]
# Lightweight agents share a simpler fallback
SMALL_MODEL_FALLBACK = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
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

# ── Diagnostic thresholds ─────────────────────────────────────────────────────
WEAK_THRESHOLD = 0.4
CLEAR_WEAK_THRESHOLD = 0.65
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
