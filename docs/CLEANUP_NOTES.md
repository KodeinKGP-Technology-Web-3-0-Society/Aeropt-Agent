# Verified cleanup

This cleanup uses Python AST inspection plus repository-wide reference searches,
including imports, tests, Makefile, CI, Docker and documentation. A module without
Python callers is not automatically dead: command-line programs and registered
tools have independent entry points. Generated caches, the virtual environment,
credentials and database migrations are outside the cleanup scope.

## Removed

| Code | Evidence and effect |
| --- | --- |
| Duplicate `main._flush_session_summary` | Shadowed the imported shared helper, called an undefined `context_manager_node` and silently swallowed the resulting error. The CLI now uses `services.turn_service.flush_session_summary`; regression tests exercise quit, EOF and Ctrl+C. |
| Unused CLI imports | `List`, `Optional`, `clear_stale_practice_carry`, `normalize_content_type` and `parse_legacy_answer` had no consumers in `main.py`. |
| `sarvam_test.py` | Unreferenced ad hoc smoke script that made a live provider request at import time. It was outside pytest's `test_*.py` discovery. Production voice support and voice tests remain. |
| `sandbox_execute._ACTION_INTENTS` | Unread dictionary; action queries and content types remain in use. |
| `utils.llm.is_quota_exhausted` | No caller; active retry handling uses `_key_failure`, `_retry_seconds` and `_note_key_failure`. |
| `utils.dimensions.dimension_table` | No caller; the physics tools resolve symbol meanings through the active quantity helpers. Its sole `Iterable` import was removed too. |
| `retrieval.store.served_count` | No caller; serving history, novelty checks and reset helpers remain. |
| `local_ingest.cnxml._find_titled_para` | No caller in the maintained CNXML parser. |
| `local_ingest.extract.fetch.archived` | No caller; catalogues already contain the source URLs used by `fetch`. |
| `local_ingest.extract.pdf.text_is_trustworthy` | No caller; removed its private regular expressions and sole `re` import. Page text extraction and page rendering remain. |
| `local_ingest.jee_syllabus.alias_index_with_subject` | No caller; active callers use `canonical_chapters` or `alias_index`. |
| Obsolete memory configuration | Removed unused `MEMORY_MODE`, `MEMORY_MODEL`, summary/window/sweeper/budget constants for a nonexistent `memory/` subsystem. Current episodic graph nodes never consumed these settings. Removed the ineffective test `MEMORY_MODE=off` setting, which did not isolate database writes. |
| Obsolete retrieval-backend example | Replaced unused `RETRIEVAL_BACKEND` with required `RETRIEVAL_DATABASE_URL` in `.env.example`; retrieval uses Postgres. |
| `agents.learning_agent._priming_messages` | No caller; active prompt construction uses the runtime context builder. |
| `agents.memory_agent._is_correct` | No caller; memory evidence uses `_grade_flag` and `_graded_outcome` without coercing missing outcomes to false. |
| Earlier `agents.practice_generator.practice_set_node` definition | Immediately overwritten by the later definition when Python imported the module. Removed that unreachable duplicate and its no-op persistence block; the effective implementation is unchanged. |

## Retained deliberately

- `local_ingest/build_corpus.py` and `concept_map.py`: superseded legacy pipeline,
  but the builder's module documentation explicitly retains it for raw cached
  NCERT text. Migrating those cached source files is a separate task.
- `local_ingest/corpus.py:openstax_items`: disabled in the current corpus assembly,
  but kept with the OpenStax parser/fetch pipeline for source compatibility. The
  active assembly currently selects NCERT, HCV, exam and reference sources.
- Offline entry points including the question architect, ingestion commands,
  retrieval CLI, harness report and deployment scripts: referenced by their own
  CLI usage, documentation, Makefile, tests or Docker.
- All agent modules: an agent can be reached through graph nodes, a handoff tool,
  an HTTP route, an offline command or a deterministic helper. An agent's absence
  from the default chat graph alone is insufficient evidence for deletion.
- Registered tool functions and FastAPI handlers: decorators dispatch them by
  name or HTTP route, so a direct Python call is unnecessary.

The removals assert no consumers inside this repository. External applications
importing undocumented internal helpers need to update those imports separately.

## Related correctness repairs

- The suggestion tool now consumes the follow-up service's list return type.
- Study surfaces use canonical `current_phase` and restore lesson planning and
  review tools. Mid-turn tool refresh and the tool-count heuristic remain open.
- Tool handlers retain turn context through both executor boundaries. Returned
  error dictionaries are failures rather than successful cached observations.
- Handoffs have an explicit thirty-second timeout clipped to remaining explicit
  turn time. A deadline does not terminate an already running Python worker.
- Composer drafts cannot emit unchecked tokens through inherited stream callbacks.
  Legacy draft database writes remain a separate audit finding.
- Session close checks chat ownership/deletion. Study messages verify both chat
  ownership and its pairing with the requested Study Room before accessing carry.
- Classroom specialist tests mock the planner model boundary and cover proposal
  filtering, preventing accidental live-provider calls in that test module.

## Validation

Validation used the checkout's Python 3.10 virtual environment; CI/container
targets Python 3.12. No production model behavior or deployment is certified.

| Check | Result |
|---|---|
| Vetted offline runtime/cleanup/physics/practice/memory/LaTeX selection (37 test modules) | 386 passed, 2 skipped, 18 subtests passed |
| Final focused regressions: session boundaries, toolbelt, suggestions, experts/checker, loop, turn budget, classroom specialists and CLI lifecycle | Passed (exit 0) |
| API authentication, chat history, voice and SSE tests outside the Windows sandbox | 33 passed |
| `python -m evals.runner --mode replay` | All 25 scenarios passed |
| `node --test cursor_gateway/test/*.test.mjs` | All 14 protocol tests passed; this does not validate tutor-to-gateway integration |
| Syntax compilation of application Python packages and entry points | Passed |
| Tool catalog | All 24 tool and four helper JSON examples parsed and validated against current Pydantic argument models |
| `git diff --check` | Passed |

The first all-tests attempt was stopped when Windows sandbox socket creation
blocked FastAPI TestClient before application dispatch. The authentication module
then passed all 18 cases outside the sandbox. The final combined API/voice/SSE
run passed all 33 cases.

A broader source-dependent cleanup run found three existing failures in
`tests/test_math_syllabus.py`: this checkout lacks the cached `.local-run` NCERT
and OpenStax source data those cases build from. Embedding-weight and configured
retrieval-database checks were not included in the vetted offline selection.
An unrestricted full-suite pass is therefore not claimed.

Reproduce the focused changed-boundary checks with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_session_boundaries.py tests/test_toolbelt.py tests/test_suggest_next_material.py tests/test_experts_and_checker.py tests/test_agent_loop.py tests/test_turn_budget.py tests/test_classroom_specialists.py tests/test_cli_lifecycle.py -o addopts= --tb=short -q
.\.venv\Scripts\python.exe -m evals.runner --mode replay
node --test cursor_gateway/test/*.test.mjs
```

## Naming and structure follow-up

The 22 October [module cleanup](MODULE_CLEANUP.md) moves provider pooling,
personalization, prerequisite selection, and fixed study-plan generation to
accurately named modules. It removes the retrieval forwarding module, the old
intent gate, the retired prompt builder, and the unreachable suggestion-selection
branch. That guide records compatibility decisions and current validation.
