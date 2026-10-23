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
# committed. utils/provider_pool.py rotates through them and retires rejected ones.
from utils.provider_pool import load_gemini_api_keys, load_sarvam_api_keys  # noqa: E402

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
# A slow correct uses outcome 0.7. Above 0.70 that can lower a stored score.
# This is the retained release rule, not an accident. Buttons and Study send no
# time today, so dampening runs only when a valid positive time is recorded.

# Study Room difficulty and session outcome. Same numbers the tools already used.
STUDY_DIFFICULTY_UP_STREAK = 3
STUDY_DIFFICULTY_DOWN_STREAK = 2
STUDY_DIFFICULTY_MIN = 1
STUDY_DIFFICULTY_MAX = 5
STUDY_MASTERED_MIN_QUESTIONS = 4
STUDY_MASTERED_RATE = 0.75
STUDY_MASTERED_HARD_QUESTIONS = 3
STUDY_MASTERED_HARD_RATE = 0.7
STUDY_MASTERED_HARD_DIFFICULTY = 3

# Paid voice routes stay off unless this is set. Speakers and health stay public.
VOICE_SPENDING_ENABLED = os.getenv("VOICE_SPENDING_ENABLED", "false").lower() in {"1", "true", "yes"}
MAX_VOICE_BYTES = int(os.getenv("MAX_VOICE_BYTES", str(5 * 1024 * 1024)))

# ── Socratic routing thresholds ───────────────────────────────────────────────
SOCRATIC_PREREQ_MIN = 0.7
SOCRATIC_TARGET_MAX = 0.4
SOCRATIC_MAX_TURNS = 3            # auto-escalate to Solver after this many Socratic turns

# ── Quiz trigger ──────────────────────────────────────────────────────────────
STRUGGLE_STREAK_QUIZ_TRIGGER = 3

# ── Context Manager ───────────────────────────────────────────────────────────
CONTEXT_MANAGER_EVERY_N = 5      # summarise every N interactions

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

# ── Mock Test ─────────────────────────────────────────────────────────────────
# Maximum questions per subject section. Prevents extremely large test builds.
MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT = 60
# Default test duration in minutes (used when config omits total_time_minutes).
MOCK_TEST_DEFAULT_TIME_MINUTES = 180
# Difficulty defaults per exam (same as EXAM_DIFFICULTY_TARGET in retrieval/service.py).
MOCK_TEST_DIFFICULTY_DEFAULTS = {"JEE_ADVANCED": 4, "JEE_MAIN": 3, "custom": 3}
# DB target_exam values allowed per exam key (mirrors retrieval/search.py EXAM_ALLOWED).
EXAM_ALLOWED_DB = {
    "JEE_ADVANCED": ("Jee Advanced", "Jee main and Advanced"),
    "JEE_MAIN": ("Jee main", "Jee main and Advanced"),
    "custom": (),   # no exam filter — all target_exam values included
}
# Model used for the post-test debrief narrative (one LLM call per test result).
MOCK_DEBRIEF_MODEL = GEMINI_MODEL
MOCK_DEBRIEF_FALLBACK_MODELS = FALLBACK_MODELS
