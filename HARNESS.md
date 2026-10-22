# Harness engineering plan for the tutor agent

Historical snapshot: this plan describes the 17 October implementation, not the
current module tree. Main Tutor context is now built by `agents/runtime/context.py`;
see [current orchestration](docs/ORCHESTRATION.md) and the
[module cleanup map](docs/MODULE_CLEANUP.md) for current boundaries.

Status: plan, not started (written 2026-10-17 against commit `b4bb310` plus the working tree).
Scope: `Aeropt-agent/` only. One phase = one commit in this repo (root `CLAUDE.md`, invariant 8).

**What "harness" means here.** It is everything around the model that decides what the model
sees, what it may do, how long it may take, what is checked before a student reads the result,
and how we know whether a change made things better. That covers context assembly, the tools,
the loop, model access, the output gate, tracing and evals. The model id is not part of it. On
this platform, the harness is what controls quality, latency and cost.

**Rule for this whole plan: measure first, then evaluate, then change behaviour.** Phase 0 adds
the numbers and Phase 2 adds the regression suite. Every phase after that must show its effect
on both. The only exceptions are the three correctness fixes in Phase 1, which are bugs whatever
the numbers say.

---

## 1. The harness today (verified)

```
api_server ─ bootstrap ─ memory_recall ─┬─ graded_fast_path ─┐
                                        └─ learning_agent ───┴─ memory_commit ─ context_manager
learning_agent:
  _build_system_prompt (skills/learning_agent.yaml + memory + progress digest + cross-room + summary)
  _decide_move_and_reveal (scaffolding policy)
  run_agent_loop  (agents/runtime/loop.py)  ≤ 6 steps, 45 s budget, forced tool-free close
     └─ Toolbelt.call (agents/runtime/toolbelt.py)  23 tools, pydantic args, scopes, read-cache
          └─ consult_expert → nested run_agent_loop (analysts: 3 steps / 20 s, read scope only)
  provenance: strip_foreign_images, check_exam_claims, render_inline_tikz
  proof gate:  ensure_proof (agents/runtime/proof.py)
  check_answer_streaming (agents/answer_checker.py)  fail-open, may rewrite, streams a rewrite
  trace.persist → agent_trace
model access: utils/llm.py (chat_completion / complete_text / stream_completion) → utils/provider_pool.py
              → Gemini's OpenAI-compatible endpoint, free-tier key pool
```

Keep these. They are already good harness design:

- a bounded loop with a forced, tool-free closing answer, so a reply always arrives;
- tools never raise, and errors come back as text the model can act on, including the list of
  valid tool names;
- capability scopes enforced in code (`EXPERT_SCOPES`), not requested in a prompt;
- an identical read inside one turn is served from cache with a "you already did this" note;
- a fail-open checker that records its verdict, so skipped checks show up in data;
- deterministic provenance checks (invented images, invented "JEE 2016" labels);
- per-key cooldown and RPM tracking in the key pool.

---

## 2. Findings, ranked by harm

Each one names its evidence, so the implementer can confirm it before changing anything.

### Correctness and honesty

**F1. The proof gate tells students an answer was verified when it wasn't.**
`agents/runtime/proof.py::_force_one`: when the tutor did not run a check tool, it runs
`check_dimensions` on the first `x = …` it finds, or else `calculate` on **the first number in
the draft** (e.g. `"2"`). It then appends `"Checked with calculate before this reply."` to the
student's answer. Nothing about the answer was checked, but the student is told it was.

**F2. The checker's rewrite bypasses the provenance filters.**
`learning_agent_turn` runs `strip_foreign_images` → `check_exam_claims` → `ensure_proof` on the
draft, and *then* `check_answer_streaming`. A revised answer goes straight to the student
(streamed via `on_token`) and is stored as `response`. Nothing re-checks it, so an invented
image or a made-up "JEE Advanced 2016" label introduced by the rewrite reaches the student.

**F3. One LLM call can outlive the turn's 45 s budget by minutes.**
The clients are built with the OpenAI SDK defaults (`openai 3.20`: read timeout **600 s**; the
pool sets `max_retries=0`, but `utils/llm.py` retries `MAX_RETRIES = 2` per model across the
fallback chain). `run_agent_loop` only checks `deadline` *between* steps. A nested
`consult_expert` loop has its own 20 s budget, which is not subtracted from the parent's. The
answer checker has no deadline at all. The SSE keepalive stops the browser from aborting, so
the student just waits.

### Cost and quota (the binding constraint)

**F4. Nobody counts tokens or LLM calls.** Nothing reads `usage` from responses (`grep usage
utils/llm.py` finds nothing). `config.py` records a measured free-tier limit of **~20 requests
per day per key per model**. A tutor turn can use up to 6 loop steps, plus 1–2 checker calls,
plus up to 3 per expert consultation, plus the background summary and coach. There is no
per-turn call budget and no metric for it.

**F5. Every step resends ~11k input tokens of mostly fixed text.** Measured on 2026-10-17:
23 tutor tool schemas = 27.7k chars (~6.9k tokens; the largest is `consult_expert` at 2.9k
chars), and the `learning_agent.yaml` template = 12.7k chars (~3.2k tokens). On top of that
come the filled sections (memory, progress digest, cross-room activity, summary) and every
earlier tool result. **Tool results are never truncated** (`ToolResult.to_json` serialises the
whole payload), so a retrieval pack is resent on every later step. Six steps is roughly 70k+
input tokens for one student message.

**F6. The prompt layout prevents prefix caching.** The student-specific sections
(`{{learner_memory}}`, `{{interaction_count}}`, `{{session_summary}}`) sit at lines 20–30 of
the system prompt, before ~2.5k tokens of fixed instructions. Provider-side implicit caching
only works on an identical prefix, so the fixed part is paid for in full on every call.

### Policy living in the harness

**F7. The scaffolding policy is keyword routing, which the architecture says not to do.**
`learning_agent._decide_move_and_reveal` matches `"give up" | "show me" | "tell me" | "answer" |
"stuck"` and sets the reveal level from `interaction_count`, which is the turn number **for the
whole session**, not for the current problem. So turn 5 of a session gets "They are stuck. Show
solution up to the sticking point" even if it is the first message about a new problem. It also
fires on any message containing "answer" ("what does the answer mean?").

### Latency

**F8. Time to first token equals the whole turn.** The draft is never streamed. The student
sees progress events, then the full answer only after the loop **and** the checker have
finished. Only a checker *rewrite* streams. Every extra loop step adds directly to the wait.

**F9. Parallel tool calls run one after another, and tools have no timeout.**
`run_agent_loop` loops over `calls`. Independent reads (`retrieve_content` +
`get_learner_context`) wait on each other, and a slow tool blocks until it returns.

### Seeing and proving behaviour

**F10. A turn cannot be replayed or attributed.** `AgentTrace.to_row` stores tool steps with
clipped previews. It stores no prompt version, no tool-schema version, no token counts, no
`stop_reason`, no per-step input size and no messages. When a reply is bad, nobody can rebuild
what the model saw, or say which prompt edit caused a regression. (`skills/*.yaml` are edited
in place and have no version id.)

**F11. Agent behaviour has no regression suite.** `tests/test_agent_loop.py` covers the loop
mechanics with a scripted model, which is good. The only behavioural eval,
`tests/test_grounding_eval.py` (20 cases: "did it retrieve?"), is opt-in, uses live models,
and writes sessions to whatever database `config` points at (hosted in `.env.local`) under a
hard-coded user id. Nothing checks multi-turn behaviour: no answer revealed on the first
attempt, citations real, memory used, the checker not degrading answers.

### Smaller

- **F12.** Context assembly is spread out. `_with_progress` adds the progress digest and
  `cross_room.recent_activity` (several DB reads) on every turn, `memory_recall_node` adds
  `episode_context`, and bootstrap adds summaries. No single place owns the budget or the order.
- **F13.** There is no circuit breaker. A tool failing the same way with *different* arguments
  keeps using steps (the read-cache only catches identical arguments).
- **F14.** The docs have drifted. Root `CLAUDE.md` says "Groq by default", but
  `config.LLM_BASE_URL` is Gemini's endpoint and the pool is Gemini keys. `loop.py`'s type
  hints name an undefined `Groq`. That works only because annotations are strings.

---

## 3. Target shape

Seven pieces, each owned by one module. Most of them already exist and need tightening rather
than replacing.

| Piece | Module | Owns |
|---|---|---|
| Turn budget | `agents/runtime/budget.py` (new) | deadline, LLM-call cap, token cap for the whole turn, shared with experts and the checker |
| Context builder | `agents/runtime/context.py` (new) | one ordered, budgeted assembly of every prompt section; fixed text first |
| Tool layer | `agents/runtime/toolbelt.py` | per-surface tool sets, result shaping, per-tool timeout, parallel reads, circuit breaker |
| Loop | `agents/runtime/loop.py` | deadline-aware calls, usage capture, budget enforcement |
| Release gate | `agents/runtime/release.py` (new) | the single ordered filter every student-facing text passes, *after* the checker |
| Trace v2 | `agents/runtime/trace.py` | tokens, versions, stop reason, replay transcript |
| Eval harness | `evals/` (new) | cassettes (recorded model replies), scenarios, judges, CI tiers |

---

## 4. Phases

### Phase 0: instrument (no behaviour change)

1. **Capture usage.** In `utils/llm._run_chain`, read `response.usage` (`prompt_tokens`,
   `completion_tokens`, plus cached tokens if the provider reports them) and return it next to
   the message. Keep the existing return types for the ~20 callers by adding a context-var
   accumulator, `utils/llm.usage_meter`, rather than changing every signature.
2. **Trace v2.** Add these to `AgentTrace` and `to_row`: `llm_calls`, `input_tokens`,
   `output_tokens`, `cached_tokens`, `stop_reason`, `prompt_version` (sha1 of the skill file
   bytes, first 12 chars), `toolset_version` (sha1 of the sorted schemas JSON), and
   `max_step_input_chars`. Store them in a new jsonb column `agent_trace.metrics` (migration
   `020_agent_trace_metrics.sql`, idempotent; add it to `APPLY.md` and `check_status.sql`).
   018 is marked reserved, so ask the user before using that number.
3. **Replay transcript (dev only).** With `AEROPT_TRACE_TRANSCRIPTS=1`, write the full message
   list of each loop call to `var/transcripts/<session>/<turn>.jsonl`. Never write it to the
   database, because it contains student text.
4. **Report.** Add `scripts/harness_report.py`, reading `agent_trace` from the **local**
   database only (refuse `supabase.co`, the same rule as `local_ingest/guard`). It prints, for
   the last N turns: p50/p95 latency, LLM calls per turn, input tokens per turn, checker verdict
   mix, `stop_reason` mix, tool error rate, repeated-call rate, and forced-close rate.

*Done when:* a local tutor turn writes `metrics`, the report prints all nine numbers, and
existing tests pass unchanged. **Record the baseline numbers in this file.** Every later phase
compares against them.

### Phase 1: correctness fixes (do these first; they are bugs)

1. **F1 (honest proof).** Replace `_force_one`. Extract the *final* stated result: the last
   `quantity = number unit` in the draft, or the boxed answer. Run `verify_result` (magnitude +
   units against the givens, when the givens are in the turn) or `check_dimensions` on the
   *final* formula. Then:
   - check passed: no student-visible note (or a short, specific one: "Units checked:
     N·m"), and trace `proof=passed`;
   - check failed: do not append anything; pass the failure to the checker as a known
     issue (`state["proof_issue"]`) so it fixes the answer; trace `proof=failed`;
   - nothing checkable: trace `proof=unverifiable` and **never** claim a check happened.

   Tests: a draft whose first number is irrelevant must not produce "Checked with"; a
   dimensionally wrong final formula must reach the checker as an issue.
2. **F2 (one release gate).** Move provenance (`strip_foreign_images`, `check_exam_claims`,
   `render_inline_tikz`), the LaTeX hardening already applied downstream, and the proof note
   into `agents/runtime/release.py::release(text, ctx) -> text`. Call it on whatever text is
   final, which means **after** the checker. Because a checker rewrite is currently streamed
   token by token, change the checker to stream into a buffer, run `release` on the buffer, and
   then emit it. That costs nothing measurable, since the rewrite already finishes before the
   reply is stored. Test: a stubbed checker rewrite containing
   `![x](https://example.com/a.png)` and "JEE Advanced 2016" comes out filtered.
3. **F3 (deadlines that hold).** Add `TurnBudget(deadline, max_llm_calls, max_input_tokens)`
   in `agents/runtime/budget.py`, created once per turn and carried on `ToolContext`.
   - Every model call gets `timeout=min(remaining, per_call_cap)`, using the OpenAI SDK's
     per-request `timeout=` option. Defaults: tutor step 25 s, checker 15 s, background 60 s.
     Set the pool's clients to `timeout=httpx.Timeout(30, connect=5)` as a floor.
   - `_run_chain` stops retrying or falling back once the budget's deadline has passed, and
     raises `BudgetExhausted`. The loop treats that as `time_budget` and force-closes, and that
     close gets a small reserved slice (default 8 s).
   - `consult_expert` creates a child budget, `min(own budget, parent remaining − close
     reserve)`. The checker gets `min(15 s, remaining)`. If nothing is left, the checker is
     skipped and the verdict is `skipped:budget`.

   Tests: a fake model that sleeps past the deadline still produces a forced answer within
   `deadline + reserve`; an expert cannot push the turn past its deadline.

*Done when:* these three tests and every existing test pass, and the Phase 0 report shows no
turn over `budget + 10 s`.

### Phase 2: eval harness (before any prompt, tool or policy change)

Layout under `Aeropt-agent/evals/`:

```
evals/
  cassettes/     recorded (request-hash → model response) pairs, committed, no student data
  scenarios/     YAML: scripted multi-turn conversations + assertions
  judges/        rubric prompts + calibration examples
  runner.py      replay | live | record
```

1. **Cassette replay (deterministic, runs in CI).** A `utils/llm` transport hook keyed by
   `sha256(model, messages, tools, temperature)`. In `replay` mode it returns the recorded
   reply, and a missing key fails the test. In `record` mode it calls live and saves the reply.
   Tool calls run for real against fakes: retrieval reads a small committed fixture corpus, and
   the DB is an in-memory fake following the `tests/test_personalization.py` mocking style.
   This makes whole-turn regression tests free and offline.
   *Caveat to handle:* any prompt or toolset change changes the hash. `runner.py --rerecord
   <scenario>` re-records deliberately, and the diff of assertions (not of text) is what review
   looks at.
2. **Scenarios with trajectory assertions.** Start with about 25, covering at least:
   - concept question → `retrieve_content` called, answer cites only retrieved material;
   - numeric problem → the final quantity passes `verify_result`, with no "Checked with"
     unless it truly was checked;
   - first attempt on a new problem → no full solution revealed (assert on
     `reveal` / absence of the final boxed value), whatever the session's turn count (F7);
   - "just tell me the answer" twice → full solution on the second;
   - "what does the answer mean?" → not treated as asking for the answer;
   - maths question → polite refusal (invariant 9), no tools;
   - chemistry question → retrieval scoped to chemistry, zero physics citations;
   - student refers to last session → uses `episode_context` / memory, no invented history;
   - tool failure injected → recovers, and the student never sees a raw error;
   - injected slow model → forced close inside budget (F3);
   - prompt injection inside a retrieved chunk ("ignore your instructions…") → ignored.

   Assertions use structured trace fields (tools called, order, counts, stop_reason, checker
   verdict, budget used) plus a few regexes on the final text. Avoid comparing exact prose.
3. **LLM judge (live tier only).** It scores pedagogy (scaffolding fits the attempt, a
   misconception is addressed, length) on a 1–5 rubric, with 10 hand-labelled calibration
   examples. Report agreement with the labels. Use it to rank changes, never as a CI gate on
   its own.
4. **Tiers.** `make eval` runs replay scenarios (offline, < 30 s) and becomes part of
   `make ci`. `make eval-live` runs record + judge against the **local** stack only, refuses a
   hosted `SUPABASE_URL`, and is meant to run nightly or before merging prompt changes. Move
   `tests/test_grounding_eval.py` into `evals/` as scenarios, and make it refuse hosted
   databases and create its own throwaway user.

*Done when:* `make eval` passes offline in CI, `make eval-live` produces a scored report, and
the scenario list above exists. **From here on, every PR that touches a prompt, a tool
description or the loop includes the `make eval` diff and the Phase 0 report diff.**

### Phase 3: context diet (measured against the Phase 0 baseline)

1. **Result shaping (F5).** Each `ToolSpec` gets `max_result_chars` (default 2,500; retrieval
   6,000). The full result stays in `ctx` for downstream use (checker grounding, provenance),
   but the model sees a shaped version: top items, ids, and a note "N more available, ask with
   `open_material(id)`". Earlier tool results are compacted on later steps: after the model has
   used a retrieval pack, later steps see only the cited items plus ids. Implement it as a
   message transform in the loop.
2. **Per-surface tool sets (F5).** Not by intent; that would be keyword routing again. Select
   by the *structural* surface that bootstrap already knows: chat, study (by phase), classroom
   (lesson / doubt / assess), coach. For example, the study-room `practice` phase does not need
   `draw_diagram` or `set_lesson_plan`, and chat does not need the study tools. Define the sets
   as data in one table in `toolbelt.py`, and record `toolset_version` per turn. Target: fewer
   than 15 tools per surface.
3. **Shorter tool descriptions.** Rewrite the six largest schemas (`consult_expert`,
   `solve_symbolically`, `retrieve_content`, `verify_result`, `check_dimensions`,
   `remember_about_learner`). Keep the *when to call / when not to* sentences and cut prose
   that repeats the system prompt. Target: schemas ≤ 15k chars total.
4. **Cache-friendly layout (F6).** Split `learning_agent.yaml` into `stable` (all fixed
   instructions) and `dynamic` (target exam, depth, memory, summary, roadmap, suggestions,
   scaffolding). The system message becomes stable text only. The dynamic block becomes a
   second message placed right before the history, marked as context and not instructions.
   Measure `cached_tokens` before and after. If the provider does not report caches, measure
   latency per input token.
5. **One context builder (F12).** `agents/runtime/context.py::build(state) -> Sections`. Each
   section has a char budget and a priority: memory facts, episode context, progress digest,
   cross-room, summary, recent turns. Low-priority sections are trimmed first when over the
   budget. Cross-room and the progress digest are cached per session for N minutes instead of
   being re-read every turn. Every tutor (`learning_agent`, `teacher_agent`, the coach) goes
   through it.

*Done when:* the Phase 0 report shows input tokens per turn down ≥ 40 % and LLM calls per turn
not up, and `make eval` stays green (re-recording is allowed only where the assertions still
pass).

### Phase 4: loop mechanics

1. **Parallel reads (F9).** When one model message holds several calls and all of them are
   read-scoped and non-consequential, run them in a small thread pool. Keep the message order
   of the results. Writes stay serial, in order.
2. **Per-tool timeout.** `ToolSpec.timeout_s` (default 8 s; retrieval 10 s; symbolic 6 s;
   `consult_expert` uses its child budget). On timeout the tool returns
   `{ok: false, error: "timed out, try a simpler call or answer without it"}`.
3. **Circuit breaker (F13).** After two failures of the same tool in one turn, the third call
   returns at once with "this tool is failing this turn, do not call it again", and the tool is
   left out of the schema list for the rest of the turn.
4. **LLM-call cap.** `TurnBudget.max_llm_calls` (default 8 per turn including experts and the
   checker). When it is reached, force-close. Log the cap hits; if they are frequent, the
   prompt is asking for too much work.
5. **Remove the effectively lost step.** Today `final_pass` triggers at `max_steps - 1`, so the
   model gets at most 5 tool rounds and one closing call. Make that explicit
   (`max_tool_rounds`) so the number in config means what it says.

*Done when:* the scenario timings show turns with 2+ reads faster at p50, there are new loop
tests (`tests/test_agent_loop.py` style) for the timeout, the breaker and the cap, and
`make eval` is green.

### Phase 5: pedagogical policy as state, not keywords (F7)

Replace `_decide_move_and_reveal` with a small, explicit **problem state** kept in `carry`:
`{problem_key, attempts_on_problem, hints_given, student_asked_for_answer: count}`.

- `problem_key` changes when the tutor serves or the student poses a new problem. The model
  sets it with a tiny `note_problem` signal on the existing `record_attempt` /
  `serve_practice_question` paths, or with one optional field in the final structured output.
  It is never derived from keywords.
- "Asked for the answer" is a structured flag the model sets (a `student_requests_solution`
  field on a lightweight `turn_signals` tool, or in the forced-close JSON), not a substring
  match.
- The reveal ladder (0 probe → 1 nudge → 2 principle → 3 to sticking point → 4 full) is driven
  by `attempts_on_problem` and the request count. The rule stays deterministic and testable;
  only the *inputs* change.

*Done when:* the F7 scenarios from Phase 2 pass ("turn 5 but first attempt" → no reveal;
"what does the answer mean" → no escalation; two explicit requests → full solution), and the
`learning_agent` keyword lists are gone.

### Phase 6: latency

Do these only once Phases 3 and 4 have taken their share. Each is measured as time to first
token (TTFT) and total time.

1. **Checker cost.** Skip the checker when the draft has no claims to review (no numbers or
   equations, no exam labels, under N chars). This extends today's `MIN_LENGTH_TO_CHECK` to a
   content test that reuses `proof.needs_proof`. Run the checker on the cheapest model with
   `reasoning=low` (already the default), and give it the shaped grounding from Phase 3, not
   the raw chunks.
2. **Start the reply sooner.** The checker must see the whole draft, so "stream the draft, then
   the checker" is not safe. Instead: (a) stream the *first paragraph* only when the checker is
   skipped for this draft; (b) stream progress text more often; (c) prewarm retrieval for the
   resolved concept during bootstrap (the Redis prefetch path exists; extend it to
   no-Redis in-process caching).
3. **Composer experts.** When the tutor consults a composer and copies its output almost word
   for word, the composer's draft is the answer. Detect this (similarity ≥ 0.9 between the
   expert output and the final text) and log it. If it is common, let `consult_expert` return
   `final: true` so the tutor ends the loop without an extra round-trip.

*Done when:* TTFT p50 is down ≥ 30 % against the baseline and `make eval` is green.

### Phase 7: model-access hygiene and docs

1. **Priority lanes.** Calls carry `lane = live | background`. With all keys hot for a model,
   background calls (summary, coach, memory) wait or are dropped, never live ones. This lives
   in `groq_pool` next to the RPM tracking.
2. **Model drift guard (invariant 6).** On startup, `client.models.list()` (cached 1 h) must
   contain every model id in config. A missing id logs an error and is taken out of the
   fallback chain at once, instead of failing per call.
3. **Docs.** Fix root `CLAUDE.md` "Model provider" (Gemini OpenAI-compatible endpoint, key
   pool, quotas). Add a "Harness" section to `ARCHITECTURE.md` linking this file. Fix
   `loop.py`'s `Groq` type hints.

---

## 5. The numbers that define "better"

Baseline in Phase 0; every later phase reports the delta.

| Metric | Source | Direction |
|---|---|---|
| LLM calls per tutor turn, p50 / p95 | trace `metrics.llm_calls` | ↓ |
| Input tokens per turn, p50 / p95 | `metrics.input_tokens` | ↓ (target −40 % in Phase 3) |
| Cached-token share | `metrics.cached_tokens / input_tokens` | ↑ |
| Turn latency p50 / p95; TTFT p50 | trace + SSE timing | ↓ |
| Turns over budget + 10 s | trace | 0 |
| `stop_reason` mix (answered / max_steps / time_budget / llm_error) | trace | forced closes ↓ |
| Checker verdicts (pass / revised / failed / skipped) | trace | revised ↓ over time, skipped:budget ≈ 0 |
| Tool error rate; repeated-call rate | trace steps | ↓ |
| Scenario pass rate (replay) | `make eval` | 100 % |
| Judge score (live) | `make eval-live` | ↑, never ↓ by more than noise |

---

## 6. Guardrails for whoever implements this

- **There is one registry** (`toolbelt.py @tool`). Tool sets per surface are *filters* over it,
  not a second registry.
- **No keyword or intent routing comes back.** Every selection in this plan uses structural
  facts (surface, study phase, scope, problem state), never the student's wording.
- **Fail open, but say so in the trace.** Every new skip (checker, proof, budget) records why.
- **Evals and reports never touch hosted data.** They refuse `supabase.co` URLs the same way
  `local_ingest/guard.py` does. Transcripts stay in `var/` (gitignored) and never go into the
  database.
- **Prompt edits go with eval diffs.** A change to `skills/*.yaml` or a tool description
  without a `make eval` result is not done.
- **Keep the measured numbers in this file** (Phase 0 baseline, then one line per phase), so
  the next person can see what each change bought.
