# Memory system: current design

Source snapshot: 17 October 2026, including migration 021. This describes implemented behavior, with incomplete integration explicitly identified. See memory-agent-explained.md for the detailed tutorial.

## Episode definition and storage

Conceptually, episodic memory records a student's past learning experiences: situation, tutor intervention, observable response and outcome evidence, including unknown outcomes.

The actual storage unit remains one eligible assistant reply, unique by session and assistant index. There is no multi-turn narrative extractor. Approach is a reply prefix; student_response is the submitted answer when available, otherwise the query excerpt.

Interaction ID is the first 24 SHA-256 hex characters over user/concept/content, or user/session/concept when content is absent. With no concept it is null. The content key groups across sessions and can include repeated attempts. This is a coarse grouping key, not an interaction lifecycle. Historical rows are not backfilled.

## Workflow

```text
normal chat: bootstrap -> recall -> learning agent -> commit
structured grade: bootstrap -> diagnostic/follow-up -> commit
study: bootstrap -> recall -> teacher -> commit -> optional evaluation
then: optional session summary when due
```

LangGraph StateGraph controls execution without a checkpointer. Explicit Supabase writes provide persistence. Recall/commit use deterministic rules and local embeddings. Background consolidation is the memory path that invokes a generative model.

## Assessment and feedback

Assessment permits not_requested, pending, completed, failed and persistence_failed. Pending is reserved in the builder. Ordinary chat produces unknown. Boolean grading with an attempt ID produces completed; an explicit failed-write flag produces unknown/persistence_failed.

Edge cases remain: a boolean grade without an attempt ID can retain correct/incorrect despite persistence_failed, and malformed study grade state does not consistently become failed. Episode status is reconstructed from local branches rather than simply copying diagnostic status.

Feedback is separate: an earlier explanation can retain unknown while feedback_outcome records a later result. The feedback patch does not update the earlier row's provenance fields.

## Matching and replay

Pending feedback selection filters user/concept/active status and excludes the current numeric assistant index. It prefers interaction then content matches. Concept fallback remains only without content. Numeric index exclusion still spans sessions, and feedback does not require a persisted attempt or reject attempt_record_failed.

Replay without feedback_outcome preserves the closed row's search text and vector as well as omitted feedback fields.

## Recall

Fetch newest 24 concept plus 16 recent rows, or newest 40 if concept is unknown. Score: 50% cosine, 25% concept match, 15% recency and 10% outcome/checker utility. Drop older incorrect rows when a newer fetched same-concept candidate is correct. Format up to three rows in 900 characters.

The recovery filter does not validate evidence source and works only inside the fetched set. Prompt formatting omits assessment provenance.

## Consolidation

Helpers deduplicate known attempt IDs, exclude explicit self-report, and require at least two identical normalized responses of 12+ characters for incorrect clusters, or answer_key/checker provenance for correct clusters. Missing attempt IDs fall back to episode IDs.

The production SELECT omits those fields, preventing normal query results from qualifying under the stricter rules. This is an integration blocker.

With eligible input, the LLM proposes a fact; code validates, claims a cluster, checks duplicates, writes/reuses a fact, and links episodes. Final logging INSERTs rather than UPDATEs the claim. Failed claims do not always stop processing; abandoned claims have no recovery. This is not atomic/exactly-once execution.

## Invalidation

Episode invalidation triggers a linked-support check. But retirement calls supersede_learner_memory(memory_id, None), leaving superseded_by null, which active-memory reads still accept. Unsupported facts are not actually retired by that operation.

## Deployment boundaries

Use migrations 017, 018 and 020 with the required learner schema on the user project. Course retrieval belongs in its separate database. The status command does not validate all 020 requirements. Scheduling is external.

No live deployment state or tests were verified for this documentation-only refresh. See memory-system-audit.md for exact remaining issues.
