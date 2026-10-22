# Agent architecture audit

Audit date: 18 October 2026. This document records findings from the checked-out
source, including issues found before the accompanying cleanup. Follow the
remediation status column when distinguishing repaired issues from remaining
work. Source locations are symbol-based because cleanup can move line numbers.

For the execution walkthrough, examples, and tool inventory, read
[ORCHESTRATION.md](ORCHESTRATION.md) and [AGENT_TOOLS.md](AGENT_TOOLS.md).

## What the repository actually runs

There are two live conversational graphs, a background coach, and several
deterministic services. The roster displayed by the application is not a list
of separate always-running agents.

1. Main chat: `session_bootstrap_node` loads profile/history and records the user
   message. Structured grading goes directly to `diagnostic_node` and
   `follow_up_node`; free text goes through episodic recall and
   `learning_agent_node`. Both branches commit memory and optionally summarize.
2. Study Room: bootstrap and recall precede `teacher_agent_node`. A completed
   teacher turn commits memory; session completion additionally invokes
   `evaluation_agent_node`. Summarization runs when due.
3. Delegation: the Main Tutor calls `consult_expert` to run one of eight
   temporary experts. Four compose drafts; four privately analyze. Analysts
   have read scopes and cannot recursively delegate.
4. Background: API session close or the explicit coach endpoint invokes
   `coach_user`. Episodic consolidation has its own manual CLI/Make target; its
   presence does not prove that a deployed scheduler runs it.
5. Classroom lesson/doubt/assessment behavior is primarily services, prompts,
   and scoped tools around the tutor. Study planning and slide summarization
   are callable specialists, not extra LangGraph nodes in the tutor graph.

Primary wiring: [main graph](../graph/workflow.py),
[study graph](../graph/study_workflow.py),
[expert catalogue](../agents/experts.py), and
[API entry points](../api_server.py).

## Prioritized findings

P1 means incorrect behavior or a broken primary flow. P2 means material
reliability, observability, or cost gaps. P3 means maintainability or future
hardening. These priorities describe source-level behavior; they are not claims
that an incident occurred in production.

| Priority | Finding and concrete effect | Evidence / recommended correction | Remediation status |
| --- | --- | --- | --- |
| P1 | The assistant-message serializer drops Gemini tool-call extension fields, including thought signatures. After executing a tool, the next model request fails with HTTP 400. The cassette transport had the same lossy projection, so offline replay did not cover this provider contract. | `agents/runtime/loop.py::_serialise_assistant`; `evals/cassettes.py::serialize_message` / `deserialize_message`. Preserve returned provider metadata unchanged across request history and recording/replay, with SDK-shaped signed-message regressions. | Addressed in the 21 October follow-up repair. |
| P2 | CLI cold database/model setup runs inside the first concept tool call. Its old eight-second dispatch cap is shorter than the Postgres ten-second connection timeout. A redundant full REST catalogue fetch also precedes cached Postgres resolution. | `main.py`, `agents/concept_resolver.py`, and `mcp_tools/curriculum_tools.py::resolve_concept`. Validate/warm retrieval before accepting CLI input, use the canonical cached catalogue, and give resolution an explicit reconnect allowance bounded by the turn. | Addressed in the 21 October follow-up repair: startup validation/warming, cached catalogue guard, twenty-second tool cap. |
| P1 | Request identity binding alone does not validate session IDs. Session close could flush/release another learner's session; study messages could pair an owned study session with a foreign chat session and write/read its turns. | `api_server.py::end_session` and `study_message`. Require `_owned_session` before mutation and require the study row's `agent_session_id` to match the supplied chat ID. | Addressed with ownership, deleted-session and pairing regression checks. |
| P2 | Model quota coverage is incomplete: session close can summarize/run coaching, and classroom plan calls a model, but neither route is in `_SPENDS_MODEL`. | `services/auth.py::_SPENDS_MODEL`, `api_server.py::end_session`, and `services/classroom_specialists.py::plan_specialist`. Charge every model-spending entry point or enqueue deduplicated metered jobs. | Remaining quota-policy work. |
| P3 | The optional Cursor gateway launcher configures Groq environment variables that the current Gemini model route does not read. Launching `make run-cursor` therefore does not prove tutor traffic reaches the gateway. | `cursor_gateway/run.sh` exports `GROQ_BASE_URL`/`GROQ_API_KEYS`; `config.py` defines Gemini `LLM_BASE_URL`; `utils/provider_pool.py` builds Gemini/Sarvam clients. Add an explicit provider/client configuration seam and an integration test before advertising the launcher as working. | Standalone gateway tests pass; tutor-launch integration remains stale. |
| P1 | Study tools are filtered using the wrong phase field. The API writes `current_phase`, but `surface_for_state` reads `study_phase`/`study_stage`. A practice session is classified as `study_learn`, hiding question serving and grading from the Teacher. | `agents/runtime/toolbelt.py::surface_for_state`; `api_server.py::study_message`; `models/study_state.py::StudyRoomState`. Use canonical `current_phase` and test the tools offered in each actual API state. | Addressed: canonical phase takes precedence; regression coverage added. |
| P1 | The Teacher is instructed to call `set_lesson_plan`, but the learn surface omits it. The `len(all_specs) > 15` switch also makes capabilities depend on registry size: removing two unrelated tools can unexpectedly restore missing tools. | `agents/teacher_agent.py::_pacing_block` and `_TEACHER_TOOLS`; `agents/runtime/toolbelt.py::SURFACE_TOOL_SETS` and `Toolbelt.__init__`. Make surface filtering explicit and independent of tool count; preserve the complete required Teacher contract. | Required lesson-planning/review tools restored with regression coverage. Tool-count switch remains a design issue. |
| P1 | Tool worker threads lose the caller's context variables. Progress/cancellation, usage accounting, and implicit LLM budgets do not automatically propagate into handlers or composer experts. The parallel-read layer introduces another thread boundary. | `agents/runtime/toolbelt.py::Toolbelt._call` uses `_TOOL_POOL.submit(spec.handler, ...)`; `agents/runtime/loop.py::run_agent_loop` submits `toolbelt.call` to another executor. Propagate a fresh `contextvars.copy_context()` for each submitted job. Keep explicit budget and cancellation checks as well. | Addressed at both executor boundaries; worker regression coverage added. Composer draft streams must remain suppressed deliberately. |
| P1 | A timed-out handler continues running and retains the live `ToolContext`. It may write the database or stage a late draft after the caller has already answered. The default handoff timeout is eight seconds although an analyst has a twenty-second allowance. | `agents/runtime/toolbelt.py::Toolbelt._call`; `mcp_tools/handoff.py::consult_expert`; `agents/experts.py::_ANALYST_TIME_BUDGET_S`. A Python thread cannot be forcibly stopped with `future.cancel()` once running. Add cooperative cancellation, late-result fencing, and transactional/idempotent writes; give handoffs a deliberate deadline. | Handoff timeout increased to thirty seconds and capped by remaining turn time; exhausted turns no longer start tools. Late-write fencing remains. |
| P1 | Composer experts persist drafts directly to chat before the Main Tutor reviews them. The API then updates presentation only when an assistant row already exists. Consequently the durable chat text may be an unaccepted or unchecked draft, while the student initially sees a revised final response. Multiple composers can insert multiple rows at the same turn index. | `agents/experts.py::_run_composer` calls legacy nodes; writes occur in `agents/solver.py::solver_node`, `agents/socratic.py::socratic_node`, `agents/quiz.py`, and `agents/practice_generator.py`. `api_server.py::_store_turn_presentation` updates only presentation. Make composers pure draft producers; persist one final response after checking and release. | Direct composer token streaming is now suppressed so context propagation cannot leak drafts. Durable draft persistence remains. |
| P1 | The Main Tutor does not preserve cancellation as a distinct graph outcome. A cancelled loop produces empty text; `learning_agent_turn` converts that into the generic fallback. Unlike the study graph, the main graph has no cancelled-turn commit branch. | `agents/runtime/loop.py::run_agent_loop`; `agents/learning_agent.py::learning_agent_turn`; compare `graph/workflow.py` with `graph/study_workflow.py::route_after_teacher`. Carry `cancelled` explicitly and gate memory/final persistence on it. The generic fallback itself is excluded by memory's skip-reply check; reaching commit does not prove an episode was saved. | Remaining cancellation-contract repair. |
| P1 | Physics tools can report verification success when a check could not be completed. `verify_result` does not count `satisfies=None` as incomplete; `check_student_work` can say every step is correct even when it lists unparsed steps. An agent can then confidently teach an unverified result. | `mcp_tools/physics_tools.py::verify_result`, failure aggregation around `satisfies_relation`; `check_student_work`, `unparsed_steps` and its final verdict. Return `passed: null` / `cannot_determine` for incomplete checks and require callers to preserve that uncertainty. | Remaining correctness repair. |
| P1 | Paid voice routes bypass authentication and the middleware's body/usage limits. Transcription reads the entire upload. Even with authentication enabled elsewhere, these endpoints can spend provider quota without a learner identity. | `services/auth.py::_PUBLIC` and `AgentAuthMiddleware.__call__`; `api_server.py::voice_transcribe` uses `await file.read()`. Apply authenticated voice quotas and bounded upload reads; retain only genuinely public metadata/liveness endpoints as public. | Remaining service-boundary repair. |
| P2 | Review and provenance guarantees differ between Main Tutor and Teacher. The Teacher checks its draft, but then only strips foreign images. A checker pass, short answer, disabled checker, or checker outage does not take the common final release path. | `agents/teacher_agent.py::teacher_agent_node`; compare `agents/learning_agent.py::learning_agent_turn` and `agents/runtime/release.py::release`. Apply the same final release function to every student-facing response and practice payload where appropriate. | Remaining release-contract repair. |
| P2 | Turn budgets are not one aggregate budget. Analyst child budgets have independent counters; their calls do not debit the parent counter. `max_input_tokens` is declared but not enforced, and `_forced_close` explicitly supplies `budget=None`. The Teacher creates no budget on its context, so its checker runs after the loop's budget context is reset. | `agents/runtime/budget.py::TurnBudget.child_budget`, `record_call`, and `is_exhausted`; `agents/runtime/loop.py::_forced_close`; `agents/teacher_agent.py::teacher_agent_node`; `utils/llm.py`. Use shared accounting with bounded child deadlines, token charging, and explicit closing/review reserves. | Remaining budget design work. |
| P2 | Read-scoped tools are assumed safe to parallelize, but retrieval/resolution tools mutate the shared turn context. Two concurrent retrievals can replace each other's full chunks, primary concept, and learner state. The model may see both previews while a composer/checker sees only the last completed pack. | `agents/runtime/loop.py::_is_parallel_read`; `mcp_tools/curriculum_tools.py::resolve_concept` / `retrieve_content` call `ctx.stage` and `ctx.state.update`. Declare concurrency safety separately from durable-write scopes, or merge isolated tool contexts deterministically while preserving evidence for every concept. | Remaining concurrency-contract repair. |
| P2 | Calculator resource guards inspect literal AST operands only. Equivalent computed operands bypass the configured limits, and a timed-out thread still runs. | `mcp_tools/compute_tools.py::_guard_node` / `_eval`. Safe probes `2**(1000+1)` and `factorial(500+1)` exceed configured limits without matching the guard. Check evaluated numeric operands before exponentiation/factorial, bound expression depth/output size, and use enforceable isolation for expensive symbolic operations. | Remaining resource-bound repair. |
| P2 | A Teacher phase transition changes state but not the toolbelt already created for that loop. Completing Examples can instruct the Teacher to start Practice immediately, although its old learn belt still cannot serve a question. | `agents/teacher_agent.py::teacher_agent_node` constructs one belt before `run_agent_loop`; `mcp_tools/study_tools.py::complete_lesson_part` / `move_phase`. Refresh schemas after structural transitions, or deliberately end the turn and start the new phase on the next invocation. | Remaining mid-turn transition repair. |
| P2 | The live context builder loses structured pending suggestions and roadmap hints. The old prompt helper supplied material IDs; `context.build` does not. A reply such as "the second one" now depends on truncated prose history rather than the explicit selection catalogue. | The retired prompt helper versus the live `agents/runtime/context.py::build`; `services/turn_service.py::update_carry` still preserves `follow_up_suggestions`. Include compact numbered suggestions with IDs and roadmap context in the live builder. | Remaining context-contract repair. |
| P2 | `turn_signals` and `get_study_context` are registered but absent from every surface allowlist. `turn_signals` accepts `student_requests_options` without applying it. `record_attempt` is withheld from normal chat, although free-text attempts can occur there. These are dormant capabilities for those paths, not unused Python modules. | `mcp_tools/learner_tools.py::turn_signals` / `record_attempt`; `mcp_tools/study_tools.py::get_study_context`; `agents/runtime/toolbelt.py::SURFACE_TOOL_SETS`. Deliberately enable required capabilities or retire their prompt contracts. Teacher context is preloaded, so keeping `get_study_context` unavailable there can be intentional. | Remaining contract cleanup and product decision for chat grading. |
| P2 | The proof gate treats any named checking tool in the trace as proof, even a failed call, a check of another expression, or a calculation unrelated to the final answer. | `agents/runtime/proof.py::already_checked` searches `trace.path()` strings without inspecting success, arguments, or the target expression. Associate verification artifacts with the exact claim and require a successful relevant result. | Remaining proof-contract repair. |
| P2 | Context summarization is synchronous in both compiled graphs despite the module docstring saying it never blocks. Graph-owned assistant text is not consistently persisted before this node executes. Summary exceptions also return `error`, marking an otherwise completed tutoring turn failed. | `agents/context_manager.py` module contract and `context_manager_node`; edges in both graphs; API post-graph persistence in `_persist_turn_side_effects`. Persist final text first, then summarize through an idempotent background job, or explicitly budget synchronous summarization and fail it independently. | Remaining lifecycle refactor. |
| P2 | The next-material tool treats a list returned by `generate_suggestions` as a dictionary, so selecting follow-up material fails rather than staging the suggestions. | `mcp_tools/learner_tools.py::suggest_next_material`; `mcp_tools/generate_suggestions.py::generate_suggestions`. Consume the returned list directly and test the real result shape. | Addressed with regression coverage. |
| P2 | The CLI shadows the shared summary-flush function with a local function that refers to an unimported `context_manager_node`; its broad exception handler hides the resulting `NameError`. | Original `main.py::_flush_session_summary`; the shared implementation is `services/turn_service.py::flush_session_summary`. Remove the shadow and keep the shared implementation. | Addressed by accompanying cleanup. |
| P3 | Two `practice_set_node` definitions occur in the same module; the first one can never run. A nested `pass` block only comments on an update that never occurs. | Original `agents/practice_generator.py::practice_set_node` definitions. Remove the shadowed definition and placeholder block, retaining the effective implementation. | Addressed by accompanying cleanup. |
| P3 | Some legacy prompt helpers and comments still describe a different execution path. Tests for a helper can pass even though the live path bypasses it. | Retired Main Tutor prompt helpers; `agents/context_manager.py` async claim; summarizer's outdated "integration is Phase 5" claim. Test live boundaries, move shared behavior into the live builder, and update wording. | Legacy `_priming_messages` and `_build_system_prompt` removed; memory/episode tests now exercise the live context builder. Summarizer comments corrected. Missing live prompt sections remain a separate behavior issue. |

### Safe local reproductions used during audit

No network requests, credentials, or learner records were required for these
checks:

- Import the tutor/study tool modules, select `_TEACHER_TOOLS`, then construct a
  `Toolbelt` for `{study_session_id: "ss", current_phase: "learn"}`. Before the
  fixes there were seventeen candidate tools, but the final names lacked
  `set_lesson_plan`. With `current_phase: "practice"`, the final names also
  lacked `serve_practice_question` and `grade_practice_answer`.
- Set a temporary `ContextVar` to `caller-context`, call a harmless test tool
  that returns the variable, and inspect the result. Before context propagation
  fixes it returned the variable's default, `missing`.
- Inspect `insert_chat_turn` in `db/supabase_client.py`: it inserts the supplied
  content. Inspect `update_chat_turn_presentation`: it updates only
  `presentation`. Thus draft persistence cannot be repaired by the presentation
  update itself.
- Tool audit probes found `verify_result(answer="a = g", relation="log(-m) = 0")`
  reports a passed result despite an indeterminate relation, and
  `check_student_work` can endorse `v = mystery(h)` despite an unparsed step.
  No expensive expression was needed to demonstrate the resource-guard bypasses.

These probes establish local behavior. They do not measure how frequently a
model selects a tool or expert in deployed traffic.

## Agent and specialist reachability

"Reachable" means there is a concrete call path in this checkout. It does not
mean production traces prove recent usage. Remove an agent only after both
static reachability and retained trace data support removal.

| Module or role | How it is used | Status |
| --- | --- | --- |
| `session_bootstrap.py` | Both graphs' entry node. | Active deterministic setup. |
| `learning_agent.py` | Main graph free-text branch; owns chat/classroom tutor turns. | Active autonomous tutor. |
| `teacher_agent.py` | Study graph teacher node. | Active autonomous teacher; tool exposure defects described above. |
| `evaluation_agent.py` | Study graph evaluation branch and explicit API phase advance. | Active specialist. |
| `learning_coach.py` | API session-close background thread and explicit coach endpoint. | Active background agent; deployment scheduling is separate. |
| `experts.py`: `worked_solution` | `consult_expert` → `_solver_node` → `solver_node`. | Reachable on demand. |
| `experts.py`: `socratic_guide` | `consult_expert` → `_socratic_node` → `socratic_node`. | Reachable on demand. |
| `experts.py`: `practice_set` | `consult_expert` → `_practice_node` → effective `practice_set_node`. | Reachable on demand. |
| `experts.py`: `quiz_builder` | `consult_expert` → `_quiz_node` → `quiz_builder_node`. | Reachable on demand. |
| `experts.py`: `verifier` | `consult_expert` → read-scoped analyst loop. | Reachable on demand. |
| `experts.py`: `error_locator` | Same analyst loop, first-error prompt. | Reachable on demand. |
| `experts.py`: `alt_explainer` | Same analyst loop, alternative-explanation prompt. | Reachable on demand. |
| `experts.py`: `comparer` | Same analyst loop, distinction/comparison prompt. | Reachable on demand. |
| `answer_checker.py` | Main Tutor and Teacher review. | Active reviewer; fail-open behavior must be visible in telemetry. |
| `diagnostic.py` | Structured main-graph fast path, progress tool, study grading, classroom assessment. | Active proficiency/attempt service with optional classification. |
| `follow_up.py` | Main-graph structured grading fast path. | Active deterministic follow-up service. |
| `concept_resolver.py` | Curriculum `resolve_concept` tool, retrieval setup, API prefetch. | Active resolution service. |
| `services/personalization.py` | `retrieval/bridge.py` calls `compute_personalization_strategy`; API prefetch calls `personalize_for_state`. | Active personalization heuristic; not a live routing agent. |
| `retrieval/bridge.py` | Curriculum retrieval tool, classroom material loading, API prefetch. | Direct state adapter; redundant agent wrapper removed on 22 October. |
| `retrieval/gate.py` | Active gate and optional `is_meta_repair_turn` classifier. | Classifier merged here; obsolete intent-based skip helper removed on 22 October. |
| `services/prerequisites.py` | Diagnostic/follow-up prerequisite checks. | Active deterministic prerequisite helper. |
| `memory_agent.py` | Recall and commit in both graphs; history deletion invalidation. | Active episodic-memory lifecycle. |
| `memory_consolidate.py` | `python -m agents.memory_consolidate`, `make memory-consolidate`. | Available maintenance command; no recurring scheduler wiring was found. |
| `memory_status.py` | API startup logging and `python -m agents.memory_status`. | Active health/diagnostic command. |
| `context_manager.py` | Both graphs when due; shared explicit session flush. | Active summarizer. |
| `services/study_plan_generator.py` | `services/classroom_specialists.py::plan_specialist` calls `generate_study_plan`. | Active plan specialist. |
| `content_summarizer.py` | `utils/presentation.py` imports `summarize_to_slide_cards`. | Active presentation specialist; keep it. |
| `agents/runtime/*` | Tool dispatch, context, budgets, loop, trace, proof, provenance, release. | Active infrastructure; not extra pedagogical agents. |

There is no evidence to call all of the legacy-named agent modules dead. Most
are now services or expert adapters. The stronger cleanup candidates are
shadowed definitions, no-op blocks, obsolete imports, unused entry points, and
outdated documentation. Conditional classifiers and maintenance CLIs should be
marked optional/manual instead of deleted merely because they are outside the
main graph.

### How to prove an expert is unused in deployment

Use `agent_trace.steps`, inspecting `kind == "handoff"` and the expert `name`.
Group by expert, route, model/prompt version, and time window. Also count failed
handoffs and unavailable-tool errors: zero successful calls can mean a broken
capability rather than an unnecessary one. Some deterministic graph paths do
not have a full `AgentTrace`, so trace absence alone is not a universal usage
counter. Add consistent traces before removing functionality based on numbers.

## Improvements to make before adding agents

1. Establish one turn lifecycle: canonical input → context → bounded reasoning
   and tools → verification → release → one final persistence transaction →
   memory/event commit → optional background work. Give every stage a turn ID.
2. Make `Surface`, `StudyPhase`, attempt IDs, and outcome types explicit. Test
   that every tool referenced in an active prompt is actually offered on its
   surface. Explicitly classify local state-changing tools rather than marking
   them read-only to satisfy a scope filter.
3. Put durable writes behind an idempotent service keyed by turn/attempt IDs.
   Retryable grading must not double-count proficiency, session counters, or
   events. Bind writes to a live turn token so late tool completions cannot
   modify a superseded turn.
4. Share budget accounting across parent, expert, checker, and forced close.
   Propagate cancellation through each thread boundary and check it before
   each durable write. Use a monotonic clock for elapsed-time deadlines.
5. Make checker failure, skipped review, unverified proof, and unresolved
   retrieval first-class outcomes. Let release choose an appropriately limited
   response instead of treating every failure as a successful reviewed answer.
6. Keep memory and source material as explicitly attributed data. Do not allow
   a stored learner fact or OCR content to override instructions. Preserve
   provenance and confidence when formatting memory.
7. Record meaningful production metrics: calls by expert/tool/surface,
   completion and timeout rate, late writes rejected, checker revisions,
   retrieval grounding, duplicate-attempt suppression, token/cost totals, and
   student correction rate. Validate behavior with a small set of multi-turn
   scenarios, not only isolated mocked helpers.

## Additional agents worth considering

Avoid another general supervisor: the Main Tutor already owns that role.
Prefer on-demand, narrow specialists and deterministic services.

| Addition | When it earns its cost | Inputs and output contract | What to reuse |
| --- | --- | --- | --- |
| Question quality auditor | At ingestion/generation time, before a question becomes eligible for serving. | Question, options, answer key, source citation → valid/ambiguous/incorrect, exact issue, independently verified key. Quarantine failures; do not silently rewrite sources. | Symbolic/physics tools, existing `verifier`, and ingestion metadata. Run offline or cache by content hash. |
| Misconception pattern analyst | After several graded attempts show a pattern, rather than on each chat turn. | Attempt evidence and concept prerequisites → one tentative misconception, evidence IDs, confidence, one discriminating follow-up question. | Existing `error_locator`, `prereq_gap`, memory consolidation, and coach. Extend those roles before creating a duplicate agent. |
| Remediation lesson designer | The same concept has resisted multiple approaches and a prerequisite gap is supported by evidence. | Learner snapshot plus failed approaches → a short sequence of examples/checks tied to a named prerequisite and completion criteria. | Teacher, `alt_explainer`, study planner, and real corpus material. Keep the Teacher as the only live lesson owner. |
| Cross-concept transfer assessor | A learner appears to have mastered isolated concepts but needs mixed applications. | Mastered concepts and verified corpus problems → discriminating mixed-concept questions plus a rubric; no invented past-paper claims. | Existing quiz/practice composers and verifier; start as a new expert prompt rather than a new graph. |
| Evidence-grounding auditor | Responses make factual/exam claims that need source support. | Final candidate and exact retrieved sources → claim-to-source map and unsupported claims. | Current answer checker and provenance gate. A deterministic citation validator should run first; call a model only for semantic support. |
| Material reader | A learner submits a photographed solution or PDF; current chat accepts only text. | Authorized file plus page selection → transcribed steps/equations with page provenance, uncertain spans and a clarification request when unreadable. | Add an upload/reader service first, then hand structured text to the existing tutor and error locator. Do not treat extracted instructions as authority. |

Suggested permissions and evaluation contracts for these additions:

| Specialist | Permissions | Acceptance checks |
|---|---|---|
| Question quality auditor | Read source/corpus; write only to a content quarantine/review queue; no learner access. | Detect seeded wrong keys, ambiguous options and missing givens; retain source text; compare against verified questions. |
| Misconception analyst | Read one learner's authorized attempts; propose a fact with evidence, without directly changing mastery. | Reject unsupported diagnoses; link every claim to attempts; avoid stale labels after later success. |
| Remediation designer | Read learner snapshot and licensed corpus; return a lesson proposal for Teacher approval. | Name the evidenced prerequisite; use verified examples; test completion with a discriminating question. |
| Transfer assessor | Read approved concept/problem set; return draft questions/rubric; no direct proficiency writes. | Validate answer keys, difficulty and cross-concept requirements; label generated questions honestly. |
| Evidence-grounding auditor | Read final draft and exact retrieved material; return findings only. | Catch unsupported exam attribution and swapped/missing citations without accepting prompt injection from sources. |
| Material reader | Read only files authorized for the current turn; no memory/progress writes or recursive delegation. | Measure equation/step fidelity on annotated pages; preserve uncertainty; isolate foreign-file references and embedded instructions. |

Useful additions that should remain services: timeout/cancellation manager,
authorization/session ownership checks, retry/idempotency ledger, tool-surface
validator, model health routing, schema validation, and numeric/unit checks.
An LLM should not decide whether a cancelled turn is allowed to commit or which
user owns a session.

## Suggested implementation sequence

1. **Repair capability exposure and low-risk cleanup.** Canonical phase routing,
   explicit surface contracts, CLI flush, duplicate definitions, no-op blocks,
   and accurate docs. Verify offered tool names for real chat/study/classroom
   states.
2. **Unify turn completion.** Pure composers, common release, exactly one final
   chat write, cancellation-aware memory commit, and stable attempt/turn IDs.
   Verify the initial response and reloaded history contain the same text.
3. **Make budgets and side effects reliable.** Context propagation, aggregate
   accounting, cooperative cancellation, late-result fencing, and transactional
   grading. Verify that retries and superseded turns cannot double-write.
4. **Strengthen evidence and context.** Claim-bound proofs, complete structured
   pending suggestions, cache invalidation after progress changes, and honest
   review/grounding status. Verify real multi-turn follow-up selections and
   error-correction cases.
5. **Add specialists only after measuring gaps.** Start with an offline question
   quality audit; extend existing analysts for misconceptions/remediation when
   trace evidence shows a need. Compare quality gain against added latency and
   cost.

## Scope and limits

This is a local code and call-path audit. It does not inspect `.env` secrets,
run live model calls, mutate learner records, certify remote database policies,
or establish actual deployment traffic. Optional features can be configured
differently in deployment. The inventory distinguishes code reachability from
observed usage, and proposed specialists from agents that already exist.
