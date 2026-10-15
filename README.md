# PrepWise — Tutor Agent

LangGraph Learning Agent for JEE Physics. One Main Tutor owns each student turn:
it calls tools, may consult a temporary expert, then the Answer Checker reviews
the Teaching Answer before the student sees it.

Product architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md). If this file and
that one disagree, `ARCHITECTURE.md` wins. Retrieval: [`RETRIEVAL.md`](RETRIEVAL.md).

Port **8001**. Frontend talks to this service for `/chat`. Django (`Aeropt-backend`,
port 8000) owns auth and roadmaps. Both share one Supabase Postgres
(`public` = agent tables, `django` = Django ORM).

## Graph

```mermaid
flowchart LR
  Boot[bootstrap] --> Shape{button click?}
  Shape -->|yes| Fast[graded fast path]
  Shape -->|no| Agent[Main Tutor loop]
  Fast --> CM[context manager]
  Agent --> CM
```

Free text always goes to the Main Tutor. There is no intent enum.

| Role | File |
|---|---|
| Main Tutor | `agents/learning_agent.py` |
| Toolbelt | `agents/runtime/toolbelt.py` |
| Temporary experts | `agents/experts.py` |
| Answer Checker | `agents/answer_checker.py` |
| Background Coach | `agents/learning_coach.py` |
| Graph | `graph/workflow.py` |
| HTTP | `api_server.py` |

## Retrieval

Three backends, chosen by `RETRIEVAL_BACKEND`:

| | `supabase` (default, cloud pgvector) | `local` (SQLite + static embed) | `neo4j` (legacy Aura) |
|---|---|---|---|
| Store | Supabase pgvector (`stecalgmpaulkkwqlqcx`) | `.local-run/index` (SQLite + FTS5 + `.npy`) | Neo4j Aura + Supabase |
| Query embedding | static / Gemini | static model, <1 ms | Gemini, ~1175 ms |
| Neo4j required? | **No** | **No** | Yes |
| Syllabus | 1,016 concepts, 44 chapters | 1,016 derived concepts, 44 chapters | 33 hand-mapped concepts |
| Corpus | 12,933 items | 10,055 items | 203 items |

### Dual Database Setup
- **User DB (`SUPABASE_URL`)**: Stores user profiles, sessions, and chat history.
- **Retrieval DB (`RETRIEVAL_SUPABASE_URL`)**: Dedicated to content chunks, concept graphs, and vector similarity search.

```bash
make index                                        # build/rebuild the local index (~4 s)
.venv/bin/python -m retrieval.cli "explain torque"  # inspect a retrieval
make retrieval-bench
```

`RETRIEVAL.md` covers how the syllabus is derived, how repeats are prevented and
when retrieval is skipped.

## Tools

Registered with `@tool`, imported from `agents/learning_agent.py::_import_tools`.

`resolve_concept` · `retrieve_content` · `open_material` · `suggest_next_material` ·
`calculate` · `get_learner_context` · `record_attempt` · `remember_about_learner` ·
`schedule_review` · `consult_expert`

Do not add a second registry in `mcp_tools/__init__.py`.

## HTTP

| Method | Path |
|---|---|
| GET | `/health`, `/api/v1/health` |
| POST | `/api/v1/chat/message` (SSE; streams after the checker) |
| POST | `/api/v1/chat/sessions` |
| GET | `/api/v1/chat/sessions`, `/api/v1/chat/sessions/{id}` |
| POST | `/api/v1/chat/end-session` |
| POST | `/api/v1/sandbox/execute` |
| POST | `/api/v1/onboarding/diagnostic` |
| POST | `/api/v1/coach/run` |

## Run
 
```bash
# 1. Activate environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment (.env)
cp .env.example .env
# Set SUPABASE_URL (User DB), RETRIEVAL_SUPABASE_URL (Retrieval DB), and LLM keys

# 3. Start Agent server (port 8001)
python -m uvicorn api_server:app --port 8001 --reload
```


Live Groq catalog checks: `RUN_LIVE_GROQ_CHECKS=1 python -m pytest tests/test_model_health.py`.

## Still open

The local corpus is grounded in OpenStax University Physics, which is **CC BY-NC-SA**
— fine locally, not for a commercial release. Replacing it is one parser; see
`RETRIEVAL.md`.

Apply `db/migrations/006_learner_memory.sql` on the live database. Without it,
memory and review tools return `saved: false` and the tutor does not remember
the student across sessions.

Do not edit nested `Aeropt-backend-main/` or `Aeropt-frontend-main/` snapshots.
