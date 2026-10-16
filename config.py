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

# Fix SSL certificate verification on Windows.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# ── Supabase (User DB) ────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "physics-diagrams")

# ── Supabase (Retrieval DB: the corpus) ───────────────────────────────────────
# A separate Supabase project. The agent reads it over Postgres (retrieval/store.py);
# `make push` (local_ingest/push_index.py) is its only writer. The REST URL/key are kept
# for db.retrieval_client, which a few catalogue reads still use.
RETRIEVAL_DATABASE_URL = os.getenv("RETRIEVAL_DATABASE_URL", "").strip()
RETRIEVAL_SUPABASE_URL = os.getenv("RETRIEVAL_SUPABASE_URL", SUPABASE_URL)
RETRIEVAL_SUPABASE_KEY = os.getenv("RETRIEVAL_SUPABASE_KEY", SUPABASE_KEY)

# ── LLM APIs (Gemini with Round-Robin Key Pool) ───────────────────────────────
# Comma-separated in .env (GEMINI_KEYS). Never put keys in source: this file is
# committed. utils/groq_pool.py rotates through them and retires rejected ones.
from utils.groq_pool import load_gemini_api_keys, load_sarvam_api_keys  # noqa: E402

GEMINI_API_KEYS = load_gemini_api_keys()
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

LLM_API_KEYS = GEMINI_API_KEYS
LLM_API_KEY = GEMINI_API_KEY
LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Voice / Optional Indic APIs (Sarvam AI for STT/TTS)
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
SARVAM_API_KEYS = load_sarvam_api_keys()

# ── Model assignments per agent ───────────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview").strip()
ORCHESTRATOR_MODEL = GEMINI_MODEL
CONCEPT_RESOLVER_MODEL = GEMINI_MODEL
DIAGNOSTIC_MODEL = GEMINI_MODEL
CONTEXT_MANAGER_MODEL = GEMINI_MODEL

SOLVER_MODEL = GEMINI_MODEL
SOCRATIC_MODEL = GEMINI_MODEL
QUIZ_MODEL = GEMINI_MODEL

# ── Fallback chains ──
# Free-tier Gemini quotas are per model AND per day (gemini-3-flash: 20 requests
# a day per key, measured 2026-10-02 when all 12 keys ran out). Each fallback has
# its own quota, so the chain multiplies daily capacity.
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.6-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
SOLVER_FALLBACK_MODELS = FALLBACK_MODELS
QUIZ_FALLBACK_MODELS = FALLBACK_MODELS
SOCRATIC_FALLBACK_MODELS = FALLBACK_MODELS
SMALL_MODEL_FALLBACK = FALLBACK_MODELS

# Embeddings: retrieval/embedder.py is the single source (model id, dimension). Nothing
# here may name another embedding model.

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

# ── Memory (memory/, plan in MEMORY_AGENT.md) ─────────────────────────────────
# off    = the pre-v2 behaviour, nothing new runs.
# shadow = jobs run and write; prompts keep the old memory text, and the pack the
#          new recall would have used is logged to var/memory_shadow.jsonl.
# on     = the new recall pack goes into prompts.
MEMORY_MODE = os.getenv("MEMORY_MODE", "shadow").strip().lower()
MEMORY_MODEL = os.getenv("MEMORY_MODEL", CONTEXT_MANAGER_MODEL).strip()
MEMORY_SUMMARY_MIN_ROWS = int(os.getenv("MEMORY_SUMMARY_MIN_ROWS", "8"))      # unsummarised chat_turns rows
MEMORY_SUMMARY_MIN_CHARS = int(os.getenv("MEMORY_SUMMARY_MIN_CHARS", "6000"))
MEMORY_WINDOW_MAX_ROWS = int(os.getenv("MEMORY_WINDOW_MAX_ROWS", "12"))        # raw turns in the prompt
MEMORY_WINDOW_MAX_CHARS = int(os.getenv("MEMORY_WINDOW_MAX_CHARS", "9000"))
MEMORY_IDLE_CLOSE_MINUTES = int(os.getenv("MEMORY_IDLE_CLOSE_MINUTES", "30"))
MEMORY_SWEEP_EVERY_S = int(os.getenv("MEMORY_SWEEP_EVERY_S", "600"))
MEMORY_PROMPT_BUDGET_CHARS = int(os.getenv("MEMORY_PROMPT_BUDGET_CHARS", "2500"))
MEMORY_MAX_ACTIVE_FACTS = int(os.getenv("MEMORY_MAX_ACTIVE_FACTS", "200"))

# ── Autonomous Learning Agent ─────────────────────────────────────────────────
LEARNING_AGENT_MODEL = GEMINI_MODEL
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
ANSWER_CHECKER_MODEL = GEMINI_MODEL
# Gemini 3 thinks before answering; on the checker's review prompt that took
# ~12 s at default effort vs ~1.5 s at "low" (2026-10-02), and "low" still
# caught and fixed a planted ω = √(LC) error. Empty string = provider default.
ANSWER_CHECKER_REASONING = os.getenv("ANSWER_CHECKER_REASONING", "low").strip()

# ── Background Learning Coach ─────────────────────────────────────────────────
# The same agent, run after platform events rather than in a live conversation.
# It decides for itself whether any action is warranted — doing nothing is a
# valid and common outcome, so there is no "N wrong answers triggers X" rule.
LEARNING_COACH_MODEL = GEMINI_MODEL
LEARNING_COACH_MAX_STEPS = 4
