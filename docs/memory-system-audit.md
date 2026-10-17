# Memory system audit: current source corrections

Rechecked against working-tree source on 17 October 2026 for the documentation refresh. No application changes, test runs, live queries, or migrations were performed. Earlier test claims/session observations below belong to the previous audit and were not independently reproduced here.

These findings supersede earlier “fixed” claims where they conflict. They are source observations, not confirmed production incidents.

| Finding | Evidence and consequence | Status |
|---|---|---|
| Consolidation lacks its input fields | list_unconsolidated_episodes omits attempt IDs, student_response, evidence_source and assessment_status. eligible_clusters needs them; real rows normally yield no_repeated_outcome. | Confirmed integration gap. |
| Claim finalization conflicts with its own key | record_consolidation always INSERTs. saved/duplicate conflicts with claimed; a failed fact write leaves a blocking claim with no recovery. | Confirmed, incomplete lifecycle. |
| Claim failure is not always a hard stop | Processing stops after failed claim only when consolidation_seen returns true. | Conditional concurrency protection only. |
| Unsupported facts are not retired | retire_facts_without_active_support calls supersede_learner_memory with None; NULL is also the active-memory predicate. | Earlier finding 10 overstated completion. |
| Failed persistence can still close earlier feedback | memory_commit calls delayed feedback first; it checks boolean correctness but not attempt_record_failed or required attempt ID. | Earlier finding 3 covers only part of the lifecycle. |
| Missing attempt can retain graded outcome | Builder can set persistence_failed while retaining correct/incorrect and next_attempt if no explicit failure flag exists. | Confirmed edge case. |
| Provenance not refreshed on old row | close_pending_episode patches feedback/proficiency, not assessment/source/response. | Columns can refer to different evidence events. |
| Turn-index exclusion crosses sessions | neq(assistant_turn_index, current_index) is not scoped to current session. | Still present despite matching improvements. |
| NumPy fix is partial | Insertion is fixed; read_learner_memories still uses boolean vector checks in optional query ranking. | Ordinary no-query path avoids it. |
| Discovery is not paginated | Oldest 500 rows, no cursor; per-user scan remains newest 80. Ineligible rows stay unconsumed. | Repeated runs need not progress. |
| Readiness misses 020 | collect_status omits evidence/claim checks; consolidation_schema_ready checks older storage. | ok=true is insufficient. |
| Interaction grouping differs from old design | _interaction_id also groups user/session/concept when content is absent; content grouping spans sessions. | Documentation corrected. |
| Misconception rule is literal | Every incorrect response must match exactly after normalization and be 12+ characters; missing attempt IDs count by row. | Not semantic pattern detection or guaranteed independence. |
| Improvement filter is heuristic | Any newer correct candidate can suppress failure, without provenance checks; formatting omits provenance. | Not a mastery assessment. |

## Changes visible in source

- Diagnostic rejects non-boolean correctness.
- TutorState declares proficiency and attempt-persistence metadata.
- Ungraded episodes use unknown.
- Provided content mismatch prevents concept fallback when no interaction match exists.
- Replay without feedback preserves closed retrieval text/vector.
- Learner-fact insertion avoids NumPy truth-value conversion.
- Eligibility helpers deduplicate known attempts and constrain evidence, but their database projection is incomplete.

Existing lifecycle/unit tests do not by their existence establish the missing database integration. No new test results are asserted here.

## Follow-up priorities

Correct database projection, claim update/recovery, actual fact retirement, and assessment-to-feedback validation before calling the system complete. Verify real database behavior and failing grading traces separately. Do not invent historical evidence during migration.

---

## Earlier audit (historical record)

Evidence is from the source inspected in this working tree and from the local tests named below. Live Supabase was not modified. Earlier chats showed `law_of_inertia` proficiency move in `user_concept_state` while episode proficiency columns stayed empty; that observation matches the code, and it is not re-checked here.

## Lifecycle

```text
free text → parse_turn_input (grade only for 1/0 while a practice item is waiting)
         → bootstrap → memory_recall → learning agent
         → tools may call diagnostic.record path
         → memory_commit writes one episodic_memory row

button or waiting 1/0 → has_graded_outcome
         → graded_fast_path → diagnostic_node → follow_up → memory_commit

between sessions → memory_consolidate reads episodes → optional learner_memory fact
```

Study room: `bootstrap → memory_recall → teacher → memory_commit`, skipping commit when cancelled.

## Findings

### 1. Free-text answers are not graded — confirmed, design, kept

Severity: high for anyone typing `1`, `2`, or `A`–`D` in the CLI.

`services/turn_service.py` `parse_turn_input` sets `has_graded_outcome` only for exactly `1` or `0` when `awaiting_graded_response` is set and the content type is practice or pyq, or for an explicit `correct`/`incorrect` payload. `graph/workflow.py` `route_from_bootstrap` sends every other turn to the learning agent.

A theory check question does not set that flag. The learning agent may call `record_attempt`, or it may only say that it recorded. Reproduction: the CLI session that answered the astronaut question in prose. No code change auto-grades explanation requests.

Resolution: unchanged routing. Diagnostics stay in `assessment_status`.

### 2. A missing correctness value became incorrect — confirmed, fixed

Severity: high. A grade flag without a boolean was stored as a wrong answer and could close an older episode.

`agents/memory_agent.py` used `bool(state.get("is_correct"))`. `agents/diagnostic.py` used `state.get("is_correct", False)`.

Resolution: both require a real boolean. Otherwise the episode outcome is `unknown` and `assessment_status` is `failed`. Diagnostic does not change the score. Test: `test_missing_correctness_does_not_become_incorrect`.

### 3. A failed attempt write could still look like a grade — confirmed, fixed

Severity: medium. `graded_fast_path_node` sets `attempt_record_failed` and still returns the original `has_graded_outcome`. Commit used to store `incorrect`.

`record_attempt` did not copy `attempt_id` or the proficiency pair onto state, and it treated a diagnostic error as a normal miss.

Resolution: `attempt_record_failed` forces outcome `unknown` and `assessment_status` `persistence_failed`. `record_attempt` returns `recorded: false` when no `attempt_id` comes back, and stages the scores only when the attempt row exists. Test: `test_failed_attempt_write_is_not_authoritative`.

### 4. One attempt counted as two consolidation inputs — confirmed, fixed

Severity: medium. A teaching row and a grade row can share `attempt_id` or `feedback_attempt_id`.

Resolution: `eligible_clusters` dedupes on attempt id before counting. Test: `test_one_attempt_counts_once_and_different_mistakes_do_not_merge`.

### 5. Feedback could close a different question — confirmed, fixed

Severity: high. `close_pending_episode` fell back to the newest pending row on the same concept when `content_id` did not match, including another session.

Resolution: a provided `content_id` that matches nothing closes nothing. Concept-only fallback remains only when the grade names no content id. `interaction_id` is preferred when migration 021 is present and the ids match. Test: `test_content_mismatch_does_not_close_another_question`.

### 6. Replay rewrote closed retrieval text — confirmed, fixed

Severity: medium. Upsert kept `feedback_status` but replaced `retrieval_text` and the embedding with the replay payload, which still said feedback was pending.

Resolution: when the stored row is `next_attempt` and the replay has no `feedback_outcome`, retrieval text and embedding are left as stored. Test: `test_replay_keeps_closed_retrieval_text` and the updated replay test in `test_episodic_memory.py`.

### 7. NumPy embedding check — confirmed, fixed

Severity: medium. `insert_learner_memory` used `if vec`. A NumPy array raises on truth-value checks, the exception was swallowed, and the fact was stored with no embedding so later cosine recall could not see it.

Resolution: test `vec is not None` and `size`. Test: `test_numpy_like_embedding_is_stored`.

### 8. Incorrect rows became a specific misconception — confirmed, fixed

Severity: high. Two incorrect episodes on a concept, including a self-report and a different mistake, were enough for the model to write a misconception.

Resolution: self-reports are excluded. Distinct attempts must share the same student response of at least 12 characters. Different mistakes do not cluster. Verified strength requires `answer_key` or `checker`. Test: `test_one_attempt_counts_once_and_different_mistakes_do_not_merge`, `test_same_mistake_can_be_consolidated`.

### 9. Concurrent consolidation could double-insert — confirmed in code, partially fixed

Severity: medium. The job inserted the fact before the unique `(user_id, cluster_key)` row.

Resolution: the job inserts a `claimed` row first. The second worker sees it and stops. This is an application lock, not a multi-statement SQL transaction. Supabase REST calls are separate. The uniqueness guarantee needs migration 021, which allows `claimed`. Fake tests do not prove live concurrency.

### 10. Invalidating episodes left derived facts active — confirmed, fixed when linked

Severity: medium. `invalidate_session` only set `status=invalid`.

Resolution: facts whose `consolidation_memory_id` no longer has an active episode are superseded. Facts with no link, including rows from before migration 018, are not guessed. Other active support keeps the fact.

### 11. Recall kept a stale failure beside a later success — confirmed, fixed

Severity: low. An incorrect episode outranked a later correct one on the same concept.

Resolution: an incorrect row older than a correct row for that concept is dropped from the three-line block. Test: `test_recall_drops_a_stale_failure_after_improvement`.

### 12. Background user listing can skip people — design limitation, mitigated

Severity: low. `list_users_with_open_episodes` reads 500 rows. Without an order, the same 500 can repeat.

Resolution: oldest `created_at` first. Still capped at 500 rows per run. A larger backlog needs another run. Not a live measurement.

### 13. Episode proficiency columns stayed empty on chat — confirmed, fixed

Severity: medium. Diagnostic updated `user_concept_state`, then LangGraph dropped `proficiency_before` and `proficiency_after` because they were not declared on `TutorState`.

Resolution: both floats, `attempt_record_failed`, and `evidence_source` are declared. They are not added to carry, so they do not leak into the next turn. Commit copies them onto the episode when this turn actually has them. Test: `test_grade_copies_proficiency_onto_the_episode`.

### 14. Neo4j and the old multi-agent router — not memory defects

Retrieval is Postgres (`RETRIEVAL_DATABASE_URL`). There is no Neo4j setting. Free text always uses the learning agent. That is `graph/workflow.py`, not a failure of the memory agent.

## What was not claimed

No production root cause beyond the code paths above. Live Supabase was not migrated and was not re-queried for this audit.
