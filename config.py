import os
import warnings
import certifi
from dotenv import load_dotenv

# Suppress pydantic v1 compatibility warning on Python 3.14.
# Comes from langchain_core internals — not our code, not an error.
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pydantic.v1.*", category=UserWarning)

from pathlib import Path

_ROOT = Path(__file__).resolve().parent
# Force uvicorn reload (retry 3)
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=True)

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

# ── LLM APIs (Sarvam AI / Indic-first) ─────────────────────────────────────────
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
SARVAM_OPENAI_BASE_URL = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai/v1").strip()

from utils.groq_pool import load_sarvam_api_keys, load_gemini_api_keys  # noqa: E402

SARVAM_API_KEYS = load_sarvam_api_keys()
# LLM key and client settings (Sarvam preferred, Gemini as backward-compatibility fallback)
LLM_API_KEYS = SARVAM_API_KEYS or load_gemini_api_keys()
LLM_API_KEY = LLM_API_KEYS[0] if LLM_API_KEYS else ""
LLM_BASE_URL = SARVAM_OPENAI_BASE_URL if SARVAM_API_KEYS else "https://generativelanguage.googleapis.com/v1beta/openai/"

# Compatibility aliases
GEMINI_API_KEYS = LLM_API_KEYS
GEMINI_API_KEY = LLM_API_KEY

# ── Model assignments per agent (Sarvam AI) ──────────────────────────────────
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "sarvam-105b-conversations").strip()
SARVAM_FALLBACK_MODELS = [m.strip() for m in os.getenv("SARVAM_FALLBACK_MODELS", "sarvam-105b").split(",") if m.strip()]

ORCHESTRATOR_MODEL = SARVAM_MODEL
CONCEPT_RESOLVER_MODEL = SARVAM_MODEL
DIAGNOSTIC_MODEL = SARVAM_MODEL
CONTEXT_MANAGER_MODEL = SARVAM_MODEL

SOLVER_MODEL = SARVAM_MODEL
SOCRATIC_MODEL = SARVAM_MODEL
QUIZ_MODEL = SARVAM_MODEL

# ── Fallback chains ──
FALLBACK_MODELS = SARVAM_FALLBACK_MODELS
SOLVER_FALLBACK_MODELS = FALLBACK_MODELS
QUIZ_FALLBACK_MODELS = FALLBACK_MODELS
SOCRATIC_FALLBACK_MODELS = FALLBACK_MODELS
SMALL_MODEL_FALLBACK = FALLBACK_MODELS

# Embedding (GraphRAG Retriever only) — no chat LLM
EMBEDDING_MODEL = "gemini-embedding-2"

# ── Speculative prefetch cache (optional) ─────────────────────────────────────
# api_server.py warms retrieval for the follow-up a student is most likely to
# pick next, and reads it back on their next turn. Purely a latency
# optimisation: with no REDIS_URL set the cache is absent and every turn
# retrieves normally.
#
# NOTE: api_server.py referenced `db.redis` for this, but SupabaseClient never
# had that attribute — so every HTTP chat turn raised AttributeError inside the
# request handler and the student got the generic failure message. The CLI path
# was unaffected, which is why the graph could look healthy while chat was not.
REDIS_URL = os.getenv("REDIS_URL", "").strip()

# ── Retrieval backend ─────────────────────────────────────────────────────────
# "neo4j"  the deployed path: Neo4j Aura graph + Gemini query embeddings.
# "local"  the local path: SQLite + FTS5 + static embeddings under .local-run/index,
#          built by `python -m local_ingest.build_index`. No network per query
#          (~6 ms warm vs ~1700 ms for embed + Aura vector search), and it carries
#          the full derived syllabus rather than the three hand-mapped chapters.
#
# Set in .env.local so the deployed service keeps its own behaviour untouched.
RETRIEVAL_BACKEND = os.getenv("RETRIEVAL_BACKEND", "neo4j").strip().lower()
LOCAL_INDEX_DIR = os.getenv("AEROPT_INDEX_DIR", str(_ROOT / ".local-run" / "index"))


def local_retrieval() -> bool:
    return RETRIEVAL_BACKEND == "local"


# ── Neo4j vector index ────────────────────────────────────────────────────────
NEO4J_VECTOR_INDEX = "content_vector_index"
NEO4J_VECTOR_TOP_K = 5
NEO4J_PREREQ_DEPTH = 3
# Must be >= total Content nodes. Used as fetch_k when candidate_ids
# filtering is active so no graph-filtered candidate is missed.
NEO4J_CONTENT_CORPUS_SIZE = int(os.getenv("NEO4J_CONTENT_CORPUS_SIZE", "2000"))

# ── Retrieval Confidence ──────────────────────────────────────────────────────
# well-matched queries scored 0.7+, off-syllabus queries scored below 0.45 in calibration on 2026-09-09
RETRIEVAL_CONFIDENCE_THRESHOLD = 0.55
RETRIEVAL_CONFIDENCE_MIN_HITS = 1
# Wall-clock budget for embed + vector search combined.
#
# This was 600 ms, which is less than the Gemini embedding call alone. Measured
# on 2026-09-11: embed ~1175 ms, Neo4j vector search ~516 ms, ~1700 ms total. So
# the timeout fired on EVERY retrieval, every turn returned zero chunks, the
# tutor answered ungrounded without saying so, and each miss logged a bogus
# content-gap event. Set from that measurement with headroom for a slow call.
# Re-measure before lowering it.
RETRIEVAL_TIMEOUT_MS = 5000

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

# ── Autonomous Learning Agent ─────────────────────────────────────────────────
LEARNING_AGENT_MODEL = SARVAM_MODEL
LEARNING_AGENT_FALLBACK_MODELS = FALLBACK_MODELS

# Reasoning steps per turn. Enough for resolve -> retrieve -> verify -> compose,
# with room for one recovery, and no more.
LEARNING_AGENT_MAX_STEPS = 6

# Wall-clock ceiling for one turn's reasoning, in seconds. On overrun the agent
# is forced to answer with what it has rather than being cut off mid-thought.
LEARNING_AGENT_TIME_BUDGET_S = float(os.environ.get("LEARNING_AGENT_TIME_BUDGET_S", "45"))

LEARNING_AGENT_TEMPERATURE = 0.3

# ── Answer Checker ────────────────────────────────────────────────────────────
# A fresh review pass over every substantial reply before the student sees it.
# Fail-open: if the checker is unavailable the draft is sent unchanged, and the
# turn trace records the skip.
ANSWER_CHECKER_ENABLED = os.getenv("ANSWER_CHECKER_ENABLED", "true").lower() != "false"
ANSWER_CHECKER_MODEL = SARVAM_MODEL
# Answer checker reasoning effort (empty string for providers that don't take it)
ANSWER_CHECKER_REASONING = os.getenv("ANSWER_CHECKER_REASONING", "").strip()

# ── Background Learning Coach ─────────────────────────────────────────────────
# The same agent, run after platform events rather than in a live conversation.
# It decides for itself whether any action is warranted — doing nothing is a
# valid and common outcome, so there is no "N wrong answers triggers X" rule.
LEARNING_COACH_MODEL = SARVAM_MODEL
LEARNING_COACH_MAX_STEPS = 4
