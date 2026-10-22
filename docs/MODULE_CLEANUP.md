# Module naming and structure cleanup

22 October 2026. This refactor preserves the existing LangGraph workflows,
agent runtime, grading policy, tool identifiers, HTTP contracts, and model
request contents. The grading implementation and migration already present in
the working tree are separate work and were retained.

## Renamed or moved modules

| Previous location | Current location | Reason |
|---|---|---|
| `retrieval/local_graph.py` | `retrieval/curriculum_graph.py` | Curriculum/prerequisite queries use Postgres, not an alternate local graph backend. |
| `utils/groq_pool.py` | `utils/provider_pool.py` | Key rotation serves the configured Gemini/Sarvam transport. |
| `agents/planner.py` | `services/personalization.py` | Deterministic teaching-mode and difficulty rules, rather than an autonomous planning agent. |
| `agents/prereq_gap.py` | `services/prerequisites.py` | Shared prerequisite-gap selection used by diagnostic and follow-up helpers. |
| `agents/study_planner.py` | `services/study_plan_generator.py` | Fixed model-generated concept queue; distinct from the deterministic schedule in `services/study_plan.py`. |
| `tests/test_groq_pool.py` | `tests/test_provider_pool.py` | Tests follow the transport module's current responsibility. |
| `tests/test_retrieval_local.py` | `tests/test_retrieval_postgres.py` | Tests explicitly require the retrieval Postgres; hosted execution remains opt-in. |

The model transport remains `utils/llm.py`. Its internal helpers are now
`complete_text`, `chat_completion`, and `stream_completion`. Internal client
variables use `_llm_client`. `personalize_for_state` names the prefetch adapter;
`CurriculumGraphClient` names the curriculum-query interface. Import sites,
lazy imports, patch targets, tests, and source references were updated together.

## Removed indirection and obsolete behavior

- Removed `agents/graphrag_retriever.py`. Its only function shallow-copied state
  and forwarded to `retrieval.bridge.retrieve_for_state`. Curriculum tools,
  classroom assessment, and API prefetch now call that adapter directly. State
  copies are retained where a mutable caller state is supplied.
- Merged the optional meta/repair classifier into the active `retrieval/gate.py`.
  Its client is initialized lazily and reused; `AEROPT_GATE_LLM` still controls
  the optional path. Removed `agents/retrieval_gate.py` and its old
  `should_skip_retrieval` function, whose only consumers were tests of the old
  intent router. Tests now cover actual gate decisions and optional-classifier
  behavior and failures.
- Removed `learning_agent._build_system_prompt` and its exclusive formatting
  helpers. Production prompt construction already used `runtime.context.build`.
  Episode/memory/progress coverage now targets that live builder. This cleanup
  does not add previously absent prompt sections or retune pedagogy.
- Removed the legacy `follow_up_response` selection branch and its exclusive
  parsing/rendering helpers. The only production caller of `follow_up_node`
  is the structural grading branch; the current graph state has no intent
  channel. Suggested material is opened through the Main Tutor's tools. Quiz
  advancement, weak-prerequisite suggestions, and content suggestions remain.
- Formula-preservation coverage previously attached to an obsolete follow-up
  renderer now exercises the active presentation formatter.

Reference searches covered application imports, dynamic strings, tests,
registry initialization, CLI commands, build/deployment scripts, YAML, and
documentation. The removed helpers were private implementation details, not
HTTP routes or registered tool names.

## Deliberately retained

- `retrieval/bridge.py`: adapts retrieval packs into tutor state and attaches
  learner context/personalization; it is not an empty forwarding module.
- Prerequisite graph traversal: `concept_edges`, recursive SQL, prerequisite
  search widening, and the graph client interface still operate through Postgres.
- `CurriculumGraphClient.close()`: a no-op compatibility method retained with
  the historical client method surface. The exported `graph`/`graph_client`
  names and query behavior remain available in the renamed module.
- Empty package markers and package exports, tool registration, maintenance
  CLIs, offline ingestion, and deployment scripts.
- Graph-owned stages and the concept-context adapter still under `agents/`:
  these have meaningful integration boundaries; moving every non-loop stage
  during grading work would add unrelated churn.
- Legacy ingestion and `docker-compose.yml`: source/deployment compatibility
  cannot be disproved from ordinary Python import counts.
- Existing environment names, including the opt-in live-model test flag, and
  external tool identifiers, roster names, logging/trace labels, and API fields.

There are no replacement forwarding modules at the retired internal import
paths. External code importing those undocumented Python paths must update to
the locations above. No external HTTP/tool consumer migration is intended.

## Validation

Before cleanup, the focused grading, graph, context, memory, gate,
personalization, prefetch, classroom, provider-pool, expert, and metadata
selection passed: **118 tests and 15 subtests**.

After cleanup, the equivalent selection plus active gate and LaTeX boundaries
passed: **137 tests and 15 subtests**.

| Check | Result |
|---|---|
| Broader pytest suite, excluding the database-dependent retrieval module, with model downloads disabled | 569 passed, 4 skipped, 18 subtests passed; 3 mathematics source-data failures |
| Deterministic evaluation replay | 25/25 scenarios passed |
| Application/package syntax compilation | Passed |
| Search for retired Python import paths and helper names in executable source/configuration | No remaining consumers |
| `git diff --check` | Passed |

The three broader-suite failures are
`test_ncert_math_concept_extraction`, `test_math_prerequisite_graph_acyclic`, and
`test_full_syllabus_integration` in `tests/test_math_syllabus.py`. The required
local textbook/source caches are absent; the same failures were already
documented in `CLEANUP_NOTES.md`. That test file and the ingestion sources it
calls were not changed by this refactor. No assertions were weakened or marked
skipped to hide these failures.

Commands (from the repository root):

```powershell
$env:HF_HUB_OFFLINE = '1'
.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_retrieval_postgres.py --maxfail=10 -o addopts= --tb=short -q
.venv/Scripts/python.exe -m evals.runner --mode replay
.venv/Scripts/python.exe -m compileall -q agents graph mcp_tools models services utils db retrieval local_ingest api_server.py config.py main.py onboarding_diagnostic.py sandbox_execute.py
```

Python execution required the unrestricted runner because the Windows virtualenv
launcher could not create its interpreter process inside the filesystem sandbox.
Database-dependent retrieval tests require a populated local retrieval database;
live model/provider and deployed frontend verification were not performed.
