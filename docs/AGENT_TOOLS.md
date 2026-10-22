# Agent tool reference

This catalog is based on the Python schemas and handlers in `mcp_tools/`, the
registration and dispatch code in `agents/runtime/toolbelt.py`, and the callers
in `agents/learning_agent.py`, `agents/teacher_agent.py`,
`agents/learning_coach.py`, and `agents/experts.py`.

## What “MCP tools” means in this repository

`mcp_tools` is the project's Python package name. The repository implements an
in-process function-calling toolbelt: `@tool` adds a `ToolSpec` to a Python
registry, `ToolSpec.schema()` produces OpenAI-compatible function schemas, and
`Toolbelt.call(name, arguments)` validates a Pydantic model and invokes its
Python handler. The model-facing transport is the chat-completions tool-call
format. There is no MCP server, MCP `tools/list` or `tools/call` endpoint,
stdio MCP transport, or MCP client session in this implementation. HTTP SSE
on the chat routes streams tutor progress and answers; it is not an MCP
transport.

Do not try to connect an external MCP client directly to `mcp_tools/`. A real
MCP adapter would need explicit server registration, authenticated learner and
session binding, capability checks, and a carefully chosen exposed subset.

## Calling and interpreting tools

Examples below are valid JSON envelopes for the internal dispatcher. They are
not HTTP request bodies and do not execute merely because they appear in a
chat message. Resolve IDs from your own syllabus and corpus; names such as
`newtons_second_law` and `content-from-retrieval` are illustrative IDs.

```python
import json
from agents.learning_agent import _import_tools
from agents.runtime.toolbelt import ToolContext, Toolbelt, registry

_import_tools()
ctx = ToolContext(user_id="authenticated-user-id", session_id="owned-session-id",
                  state={"query": "Find the speed after falling 20 m"})
belt = Toolbelt(registry().values(), ctx)
call = json.loads('{"name":"calculate","arguments":{"expression":"sqrt(2*9.81*20)"}}')
result = belt.call(call["name"], call["arguments"])
print(result.to_json())
```

Only use identities established by the application. The `ToolContext` identity
is the authority for learner tools; user IDs are deliberately not model
arguments on registered tools. Study examples also require the Study Room
tools to be imported and the state described in the relevant entry.

Successful dispatcher responses have `{"ok":true,"data":...}`. Validation,
missing capability, handler exceptions, handler-returned `error` dictionaries,
and timeouts use an error envelope. Existing `ToolResult` values are preserved.
Some handlers also return domain status flags such as `saved:false`,
`scheduled:false`, `passed:false`, `exhausted:true`, or an `error` field. Read
those flags: transport success alone does not establish a correct result or a
persisted database write. Direct Python helper calls bypass Toolbelt validation,
scope enforcement, cache, timeout, and trace bookkeeping.

Read previews can be shortened before they enter model context. Full grounding
chunks remain in turn state for composition; `open_material` can select a
specific chunk. The result character limit is a shaping target, not a strict
serialization-size guarantee.

## Capability and surface policy

Scopes:

| Scope | Intended capability |
|---|---|
| `read` | Content retrieval, learner reads, computation, diagrams |
| `write:progress` | Attempts, phase changes, study statistics |
| `write:memory` | Durable learner facts |
| `write:schedule` | Review scheduling |
| `compose` | Delegate to a temporary expert |

The Main Tutor receives all five scopes. Analyst experts receive `read` only.
The background coach receives `read`, `write:memory`, and `write:schedule`.
The teacher has all tutor scopes but supplies its own candidate tool list.
`consult_expert` additionally refuses delegation when context depth is at
least one. Composer experts invoke existing node functions; they do not use
the analyst tool loop.

The following surface names refer to `SURFACE_TOOL_SETS` in
`agents/runtime/toolbelt.py`:

| Code | Surface |
|---|---|
| C | `chat` |
| SL | `study_learn` (Learn and Examples) |
| SP | `study_practice` |
| CL | `classroom_lesson` |
| CD | `classroom_doubt` |
| CA | `classroom_assess` |
| CO | `coach` |

Catalog surface columns list declared eligibility; tools also need to be
imported, selected by the caller, and allowed by its scopes. Teacher candidates
are `retrieve_content`, `open_material`, `serve_practice_question`,
`grade_practice_answer`, `transition_phase`, `complete_study_session`,
`check_student_work`, `calculate`, `check_dimensions`, `verify_result`,
`solve_symbolically`, `convert_units`, `remember_about_learner`,
`schedule_review`, `draw_diagram`, `set_lesson_plan`, and
`complete_lesson_part`. The teacher removes serving and grading while explaining
the previous answer. Context is preloaded, so the teacher intentionally does not
request `get_learner_context` or `get_study_context`.

The current implementation applies the surface filter only when the caller
supplies more than fifteen candidate specs. Smaller explicit sets still have
scope checks but bypass that filter. A fresh coach imports only a small set;
the teacher also drops to fifteen candidates when explaining a previous
answer. Effective eligibility can therefore vary by caller and import order.
Study surface inference uses `current_phase` first, then legacy
`study_phase`/`study_stage`. A toolbelt is selected once at turn start;
transitioning phase does not rebuild it in the middle of that loop.

Scope names describe intended capabilities rather than complete effect
isolation. For example, retrieval tools stage live state and
`serve_practice_question` writes a serving event and seen-content ledger even
though its declared scope is `read`. Consider those side effects when adding
tools, analyst capabilities, read caching, or concurrent execution.

## Registered tools

Optional fields default to `null` unless a different default is stated. No
registered production tool currently sets `consequential=True`; the generic
permission mechanism in Toolbelt is infrastructure, not an active confirmation
workflow.

### Content and syllabus (`curriculum_tools.py`)

| Tool | Arguments and defaults | Scope; surfaces | Result and effects |
|---|---|---|---|
| `resolve_concept` | Required `query:str`; optional `content_type_hint:str` | `read`; C, CD | Resolves syllabus concept and related concepts, reports `in_scope`; stages concept IDs and requested content type. Hint should be `theory`, `example`, `practice`, or `pyq`. |
| `retrieve_content` | Optional `concept_id:str`, `exam:str`, `query:str`, `avoid_repeats:bool`; `content_type:str="theory"` | `read`; C, SL, CL, CD, CA, CO | Returns previews, source citations, confidence, learner proficiency, weak prerequisites, and teaching advice. Stages full chunks and grounding/personalization state. Explicitly forces retrieval. Exam override accepts `JEE_ADVANCED` or `JEE_MAINS`. Defaults repeat exclusion to retrieval policy for practice/PYQ. |
| `open_material` | Required `content_id:str` | `read`; C, SL, CL, CD | Loads a specific stored item; returns preview and concept; replaces grounding chunks and records selected follow-up ID in state. It does not search for a replacement. |

```json
{"name":"resolve_concept","arguments":{"query":"Explain Newton's second law","content_type_hint":"theory"}}
```

Expected shape: `in_scope`, `concept_id`, `related_concept_ids`, `content_type`;
out-of-scope returns `in_scope:false`. A retrieval/database failure is different
from proof that the topic is outside the syllabus.

`resolve_concept` uses the cached Postgres catalogue rather than fetching the
whole REST catalogue before each lookup. Its explicit dispatch timeout is twenty
seconds, clipped to the remaining explicit turn budget. CLI startup validates
and warms retrieval before accepting a timed chat turn.

```json
{"name":"retrieve_content","arguments":{"concept_id":"newtons_second_law","content_type":"example","exam":"JEE_ADVANCED","query":"block on an incline","avoid_repeats":true}}
```

Inspect `retrieval_confidence`, `chunks_found`, `chunks`, `sources`, and optional
`guidance`, `novelty`, and `teaching_signal`. Retrieved advice is advisory;
low confidence should affect how strongly the tutor claims grounding.

```json
{"name":"open_material","arguments":{"content_id":"content-from-retrieval"}}
```

Returns `opened`, `content_type`, `concept_id`, `preview`, and `next_step`.
Use an ID returned by retrieval or a previously offered suggestion.

### Learner and next steps (`learner_tools.py`)

| Tool | Arguments and defaults | Scope; surfaces | Result and effects |
|---|---|---|---|
| `get_learner_context` | Optional `concept_id:str`; `include_weak_concepts:bool=true` | `read`; C, CO | Reads durable facts, learner settings, weak concepts, optional concept attempts, and condensed progress. Bumps memory-hit metadata for recalled facts. |
| `record_attempt` | Required `is_correct:bool`; optional `concept_id:str`, `student_answer:str`, `error_type:str`, `time_taken_sec:int`, `problem_key:str` | `write:progress`; CA | Runs `diagnostic_node`, inserts attempt and updates proficiency; stages outcome/evidence, remediation, score, and problem counters. Use once per actual attempt. Error types: conceptual, procedural, calculation, misread. |
| `remember_about_learner` | Required `kind:str`, `fact:str`; `evidence:str=""`, optional `concept_id:str`, `confidence:float=0.6` in [0,1] | `write:memory`; C, SL, SP, CL, CD, CA, CO | Inserts a durable learner fact with tutor/coach provenance. Kinds: preference, misconception, strength, context, goal. Handler requires fact length 10–300 characters. Returns `saved`, kind, fact, or unavailable-memory note. |
| `schedule_review` | Required `concept_id:str`, `in_days:int` in [1,60]; `reason:str=""` | `write:schedule`; SL, SP, CO | Upserts a UTC review due date with tutor/coach provenance; returns `scheduled`, `due_in_days`, `rescheduled_existing`. This is a review record, not a push-notification delivery service. |
| `suggest_next_material` | Optional `concept_id:str` | `read`; C, SL | Calls the plain `generate_suggestions` helper; returns up to four compact suggestions and stages full suggestions for later selection. Uses current concept and proficiency when omitted. |
| `turn_signals` | Optional `problem_key:str`; `student_requests_solution:bool=false`, `student_requests_options:bool=false`, `hint_given:bool=false` | `read`; no declared surface | Resets or increments turn-local problem counters and stages `problem_state`. Registered but excluded from current production surface lists. `student_requests_options` is accepted by the schema but is not currently applied by the handler. |

```json
{"name":"get_learner_context","arguments":{"concept_id":"newtons_second_law","include_weak_concepts":true}}
```

Returns `remembered_facts`, `target_exam`, `preferred_depth`, `weak_concepts`,
optional `this_concept` and recent attempts, plus `progress`.

```json
{"name":"record_attempt","arguments":{"is_correct":false,"concept_id":"newtons_second_law","student_answer":"a = g*sin(theta) - mu*m*g*cos(theta)","error_type":"procedural","time_taken_sec":90,"problem_key":"incline-question-1"}}
```

Requires the current turn to represent a real graded attempt. Successful
persistence returns `recorded:true`, concept, correctness, and score/mistake
summary. A failed insert must not be reported as completed learning evidence.

```json
{"name":"remember_about_learner","arguments":{"kind":"misconception","fact":"Often forgets to divide every force term by mass.","evidence":"Repeated in two incline problems today.","concept_id":"newtons_second_law","confidence":0.8}}
```

```json
{"name":"schedule_review","arguments":{"concept_id":"newtons_second_law","in_days":3,"reason":"Revisit force balance after correcting the mass cancellation."}}
```

```json
{"name":"suggest_next_material","arguments":{"concept_id":"newtons_second_law"}}
```

Suggestion IDs should come from the returned database candidates. Empty results
are valid when there are no eligible unseen theory/example resources.

```json
{"name":"turn_signals","arguments":{"problem_key":"incline-question-2","student_requests_solution":true,"hint_given":false}}
```

This last example validates against the registered schema but is rejected by
normal surface-filtered toolbelts until the capability is explicitly exposed.

### Arithmetic (`compute_tools.py`)

`calculate`: required `expression:str`; optional `purpose:str=""`. Scope
`read`; surfaces C, SL, SP, CL, CD, CA. `purpose` is trace context; it does not
change evaluation. Returns `expression`, `value`, and float formatting fields
such as `rounded_3sf` and `rounded_2dp`.

```json
{"name":"calculate","arguments":{"expression":"sqrt(2 * 9.81 * 20)","purpose":"speed after a 20 metre fall"}}
```

The example evaluates to approximately 19.81. This is a restricted Python AST
evaluator, with arithmetic, mathematical functions, numeric lists/tuples and
constants `pi`, `e`, `tau`, `inf`, `g=9.81`; it does not accept arbitrary
variables or assignment. Expressions are limited to 500 characters. Parser
restrictions are not a substitute for process-level resource bounds on costly
math expressions.

### Symbolic mathematics (`symbolic_tools.py`)

`solve_symbolically`: required `operation:str`, `expression:str`; optional
`symbol:str`, `at:str`, `substitutions:dict[str,str]`; `order:int=1`. Scope
`read`; C, SL, SP, CL, CD, CA. Tool timeout is six seconds. Operations are
`solve`, `simplify`, `expand`, `factor`, `differentiate`, `integrate`, `limit`,
`approximate`, and `substitute`.

```json
{"name":"solve_symbolically","arguments":{"operation":"solve","expression":"m*a = m*g*sin(theta) - mu*m*g*cos(theta)","symbol":"a","substitutions":{"theta":"pi/6","mu":"0.1","g":"9.81"}}}
```

Returns operation-specific fields, LaTeX/plain representations and optional
numeric substitution results. `solve` uses `solutions`; other operations
commonly use `result`, `latex`, and `plain`. Input is parsed by the restricted
math parser before AST-to-SymPy conversion. Symbols assume positivity, which
can omit negative roots; inspect notes and physical domains.

For definite integration use `at:"0 to d"`; for a limit use `at:"0"` or
`at:"inf"`; for approximation `at` is the expansion point and `order` controls
truncation. For differentiation `order` is derivative order. An equation passed
to simplify/expand/factor acts on left minus right. Numeric probes used for
limits are evidence, not an analytic proof.

### Physics verification (`physics_tools.py`)

All four tools declare `read`. Products should be written explicitly where
possible; both plain expressions and supported LaTeX are accepted. Ambiguous
symbols may require meanings such as `{"T":"temperature"}`.

| Tool | Arguments and defaults | Surfaces | Result |
|---|---|---|---|
| `check_dimensions` | Required `expression:str`; optional `expect:str`, `symbol_meanings:dict[str,str]` | C, SL, SP, CL, CD, CA | Dimensional consistency, inferred quantities, expected-dimension agreement; can flag missing or ambiguous meanings. |
| `verify_result` | Required `answer:str`; optional `relation:str`, `expect_quantity:str`, `limiting_cases:dict[str,str]`, `symbol_meanings:dict[str,str]` | C, SL, SP, CL, CD, CA | Dimension report, numeric governing-relation substitution, limiting-case probes, `passed` and explanatory verdict. |
| `check_student_work` | Required `steps:list[str]`; optional `symbol_meanings:dict[str,str]` | C, SP, CD, CA | Transitions, dimensional problems, unparsed steps, `first_problem_at_step`, and verdict. Needs at least two nonblank steps. |
| `convert_units` | Required `value:float`, `from_unit:str`, `to_unit:str` | C, SP, CD | Numeric conversion with compound-unit parsing; incompatible dimensions return an error. |

```json
{"name":"check_dimensions","arguments":{"expression":"v = sqrt(2*g*h)","expect":"velocity"}}
```

```json
{"name":"verify_result","arguments":{"answer":"a = g*(sin(theta) - mu*cos(theta))","relation":"m*a = m*g*sin(theta) - mu*m*g*cos(theta)","expect_quantity":"acceleration","limiting_cases":{"mu -> 0":"g*sin(theta)"}}}
```

```json
{"name":"check_student_work","arguments":{"steps":["m*a = m*g*sin(theta) - mu*m*g*cos(theta)","a = g*sin(theta) - mu*m*g*cos(theta)"]}}
```

```json
{"name":"convert_units","arguments":{"value":72,"from_unit":"km/h","to_unit":"m/s"}}
```

The conversion yields 20 m/s. A dimensional pass does not prove coefficients,
signs, setup, or domain restrictions. Numerical sampling can miss exceptional
cases. Inspect inconclusive and unparsed details before claiming correctness;
an absence of a counterexample is weaker than a proof.

### Diagrams (`diagram_tools.py`)

`draw_diagram`: required `tikz:str` (also accepts `tikz_code` or `code` as
validation aliases); `caption:str="Diagram"`. Scope `read`; C, SL, CL.

```json
{"name":"draw_diagram","arguments":{"tikz":"\\begin{tikzpicture}\\draw[->] (0,0) -- (2,0) node[right] {$F$};\\end{tikzpicture}","caption":"Applied force"}}
```

`services/diagrams.py` validates source, compiles with Tectonic, converts using
`pdftocairo`, and caches SVG under `var/diagrams/svg/<id>.svg` with a durable
copy when configured. Requires both binaries. Returns `diagram_id` and
`markdown` such as `![Applied force](diagram:<id>)`. API route
`GET /api/v1/diagrams/<id>.svg` serves the image. Compilation can outlast the
default tool timeout on a cold cache. Compiler failures return an error message;
fix the TikZ and retry. Handler hints are not preserved by failed-result serialization.

### Delegation (`handoff.py`)

`consult_expert`: required `expert:str`, `task:str`; `material:str=""`.
Scope `compose`; C, CL, CD. The task must be at least ten characters after
trimming and self-contained because the expert does not receive the recent
conversation as a separate transcript.

```json
{"name":"consult_expert","arguments":{"expert":"verifier","task":"Independently check that a block with mass 2 kg under net force 10 N accelerates at 5 m/s^2.","material":"F = m*a; a = 10/2 = 5 m/s^2"}}
```

Available experts from `agents/experts.py`:

| Name | Kind | Work |
|---|---|---|
| `worked_solution` | Composer | Grounded worked explanation through `solver_node` |
| `socratic_guide` | Composer | A guiding question through `socratic_node` |
| `practice_set` | Composer | Practice/PYQ set through `practice_set_node` |
| `quiz_builder` | Composer | Retrieved quiz through `quiz_builder_node` |
| `verifier` | Analyst | Independent result and uncertainty report |
| `error_locator` | Analyst | First incorrect step and error classification |
| `alt_explainer` | Analyst | A different framing plus a check question |
| `comparer` | Analyst | Deciding distinctions between concepts or methods |

Composer output stages `draft_response`, `draft_author`, `draft_model`, and
selected structured UI fields. The tutor can answer `USE_DRAFT` to retain the
draft. Analyst output is a private finding; its child updates are not lifted
into the parent. Analyst loops have up to three model steps and a nominal
20-second budget constrained by the parent turn budget. The wrapper's own
tool timeout must also accommodate the delegated work.
The delegation wrapper uses a thirty-second timeout, clipped to remaining
parent budget where the caller supplies one. A timeout stops waiting; it does
not terminate an already running Python worker or roll back its effects.

### Study Room (`study_tools.py`)

These tools are imported by the teacher and by direct study API paths, rather
than by the Main Tutor's import list. Database writes use the authenticated
`ctx.user_id`; study mutations require an owned `study_session_id` established
by the API and the current session/runtime fields in turn state.

| Tool | Arguments and defaults | Scope; surfaces | Result and effects |
|---|---|---|---|
| `get_study_context` | Optional `study_session_id:str` (defaults from state) | `read`; no declared surface | Reads session, prior concept sessions, performance, proficiency/readiness and memories. Retained registered helper; teacher instead preloads a richer snapshot. |
| `transition_phase` | Required `target_phase:str`; optional `phase_summary:str` | `write:progress`; SL, SP | Moves forward in learn → examples → practice; writes phase events and session row, resets pacing state. Evaluation/completed targets are refused; use completion tool. |
| `set_lesson_plan` | Required `parts:list[str]`, 1–4 entries | `write:progress`; SL | Persists short titles for Learn/Examples, fixes the outline once committed, initializes phase step. Titles normalized/truncated to 60 characters. |
| `complete_lesson_part` | Optional `summary:str` | `write:progress`; SL | Increments phase step; last Learn part automatically moves to Examples, last Examples part moves to Practice. Writes row and phase events. |
| `serve_practice_question` | Optional `difficulty:int`; `content_type:str="practice"` | `read`; SP, CA | Retrieves calibrated practice/PYQ, excludes served IDs, widens nearby selection, and can revisit when exhausted. Marks seen ledger and question-served event; stages current question/answer-waiting state. |
| `grade_practice_answer` | Required `is_correct:bool`; optional `student_answer:str`, `error_type:str` | `write:progress`; SP, CA | Requires a current question; logs attempt, updates statistics/difficulty and diagnostic proficiency, clears the open question, stages `last_grade`. |
| `complete_study_session` | Optional `summary:str` | `write:progress`; SL, SP | Records outcome/events, closes session/reviews, stores session memory when available, and sets evaluation phase to trigger evaluation graph node. |

```json
{"name":"get_study_context","arguments":{"study_session_id":"owned-study-session-id"}}
```

Returns concept, phase, difficulty, questions served/correct, rate and optional
prior sessions, proficiency, readiness, and concept-specific memories. This
example needs explicit exposure of the currently withheld helper. The helper
itself does not enforce session ownership; never expose arbitrary session IDs
without adding an ownership check.

```json
{"name":"transition_phase","arguments":{"target_phase":"examples","phase_summary":"Understands the net force and acceleration relationship."}}
```

```json
{"name":"set_lesson_plan","arguments":{"parts":["Identify forces","Write the net force equation","Check an incline case"]}}
```

```json
{"name":"complete_lesson_part","arguments":{"summary":"Correctly identified normal force and weight."}}
```

```json
{"name":"serve_practice_question","arguments":{"difficulty":2,"content_type":"practice"}}
```

Requires `concept_id` or `primary_concept_id`; study state also includes the
owned session ID, source, difficulty, and served IDs. A question already open
or a grade in this same turn prevents serving. Returns ID, type, difficulty,
source, opening words and `revisit`; the UI receives full question data from
state. A PYQ-only session retains that restriction throughout fallback.

```json
{"name":"grade_practice_answer","arguments":{"is_correct":false,"student_answer":"2.5 m/s^2","error_type":"calculation"}}
```

Use the official structured answer path where available: API
`POST /api/v1/study/answer` grades choice/numeric input against the stored key
before calling this persistence tool. This tool itself accepts the supplied
correctness label rather than proving it. Three correct in a row raise
difficulty; two wrong lower it, within 1–5. A grade clears `current_question`,
so the same in-memory context cannot immediately grade it again; concurrent
requests still need database-level idempotency.

```json
{"name":"complete_study_session","arguments":{"summary":"Practised five force-balance questions and corrected mass cancellation."}}
```

Outcome is `incomplete` with zero attempts; `mastered` with at least four and
75% correct, or at least three and 70% correct at difficulty at least three;
otherwise `needs_review`. Readiness is calculated later by the evaluation
node; completion itself returns outcome, counts, rate, and difficulty.

## Plain Python helpers under `mcp_tools/`

These names are exported for application compatibility. They have Pydantic
argument classes but no `@tool`, no live model schema, and no MCP transport.
Call them as `helper(ArgsModel(**arguments))`, not through `Toolbelt.call`.

### `resolve_concept_id.py`

`resolve_concept_id(ResolveConceptIdArgs)` is used by
`agents/concept_resolver.py`. Required `query:str`; `recent_turns:list[dict]=[]`,
optional `roadmap_concept_id:str`, `study_stage:str`. A validated roadmap concept
can bypass resolution; otherwise the retrieval resolver uses embeddings,
aliases and material voting. Returns `in_scope`, `primary_concept_id`,
secondary IDs, content type, and optional concept/grounding metadata.

```json
{"helper":"resolve_concept_id","arguments":{"query":"Explain centripetal acceleration","recent_turns":[],"study_stage":"theory"}}
```

This module also contains `lexical_resolve(query, concepts)` (used by practice
generation), `infer_content_type(query)`, and private normalization/local
resolution helpers. They operate inside the application and are not
independent registered tools.

### `generate_suggestions.py`

`generate_suggestions(GenerateSuggestionsArgs)` is called by
`suggest_next_material`. Required `user_id:str`, `primary_concept_id:str`;
`proficiency:float=0.5`, `seen_follow_up_ids:list[str]=[]`. Looks up unseen graph
candidates, fetches metadata, keeps theory/examples, and asks the configured
follow-up model to select up to three; falls back to initial eligible
candidates on malformed model JSON. Candidate IDs are checked against fetched
rows. Returns a **list of content dictionaries**, including reason/preview;
an empty list means no suggestions or a handled dependency failure. It is not
a dictionary with a `suggestions` field.

```json
{"helper":"generate_suggestions","arguments":{"user_id":"authenticated-user-id","primary_concept_id":"newtons_second_law","proficiency":0.45,"seen_follow_up_ids":[]}}
```

Despite its historical description, this helper can spend an LLM call and
depends on the graph adapter plus content/seen-history database reads.

### `get_syllabus_status.py`

`get_syllabus_status(GetSyllabusStatusArgs)` supports status cards. Required
`user_id:str`; `focus_chapters:list[str]=[]`, optional
`roadmap_concept_id:str`. Reads concept states and syllabus metadata, returns
`unfinished_lessons`, `weak_chapters`, next concept ID/name, and
`coverage_gap`. Cold or below-threshold states count as unfinished; a coverage
gap is at least three unfinished lessons. This is deterministic aggregation,
not an autonomous Syllabus Agent.

```json
{"helper":"get_syllabus_status","arguments":{"user_id":"authenticated-user-id","focus_chapters":["Laws of Motion"]}}
```

### `get_agent_signals.py`

`get_agent_signals(GetAgentSignalsArgs)` serves the compatibility
`GET /api/v1/agents/status` route and attached status payloads. Required
`user_id:str`; optional `session_id:str`, `roadmap_concept_id:str`,
`active_agent:str`, `primary_concept_id:str`; `focus_chapters:list[str]=[]`.
Returns `agents` cards and `active_agent` after deterministic aggregation and
`get_syllabus_status`. It performs no agent dispatch. The `session_id` field is
accepted for compatibility but is not used in current aggregation.

```json
{"helper":"get_agent_signals","arguments":{"user_id":"authenticated-user-id","focus_chapters":[],"active_agent":"tutor","primary_concept_id":"newtons_second_law"}}
```

The five card IDs `tutor`, `syllabus`, `doubt_resolver`, `practice_coach`, and
`mentor_briefing` are a retired UI roster. Their appearance in a response does
not demonstrate five running agents. `mcp_tools/__init__.py` re-exports the four
helpers above; it is not a second tool registry.

## Example end-to-end tool sequences

1. **A conceptual doubt:** resolve the concept → retrieve theory → optionally
   calculate/check a claim → optionally consult `worked_solution` or
   `alt_explainer` → checker and release gate → answer. These are model decisions,
   not mandatory repeated calls when state already supplies the concept and
   grounding.
2. **A student shows broken algebra:** `check_student_work` on literal
   transcribed steps → inspect the first decisive mismatch → retrieve material
   if needed → explain that step → remember a recurring misconception only
   with evidence. This flow accepts text; a photo is not automatically
   transcribed by these tools.
3. **A Study Room lesson:** state and learner snapshot preload →
   `set_lesson_plan` → retrieve grounding → teach and check a part →
   `complete_lesson_part` → automatic phase progression → practice serving.
4. **A structured practice attempt:** `serve_practice_question` or
   `/api/v1/study/next` → learner chooses/enters answer →
   `/api/v1/study/answer` checks stored key → `grade_practice_answer` records
   result → student reviews feedback → next question on a later request →
   completion → evaluation/readiness.
5. **Between sessions:** background coach reads activity and
   `get_learner_context` → either does nothing, writes an evidenced memory, or
   `schedule_review`. A scheduled review is stored data; an external scheduler
   must invoke coach passes and a notification channel must be implemented
   separately if desired.

## Maintaining this catalog

When changing tools, update registration, caller imports, scopes/effects,
surface allowlists, teacher candidates and this reference together. Test actual
production candidate sets, including every Study Room phase and fresh-process
coach startup. Prefer one declarative capability definition with explicit
cacheability/parallel safety and generated catalog checks to duplicate lists.

Useful local checks live in `tests/test_toolbelt.py`,
`tests/test_context_diet.py`, `tests/test_loop_mechanics.py`,
`tests/test_physics_tools.py`, `tests/test_symbolic_tools.py`, and
`tests/test_problem_state_policy.py`. A registry entry proves availability in
source; telemetry is needed to establish whether a model actually chose a
reachable expert/tool in real learner traffic.
