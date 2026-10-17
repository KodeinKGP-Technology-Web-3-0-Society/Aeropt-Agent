# Episodic memory: implementation and deployment

Updated from working-tree source on 17 October 2026. This replaces the older rollout notes. Live Supabase was not checked and migrations were not applied during this documentation refresh.

Read memory-agent-explained.md for the full tutorial, memory-system-design.md for architecture, and memory-system-audit.md for remaining gaps.

## Implemented behavior

- Compact episode per eligible assistant reply, unique by session and assistant index.
- Deterministic recall before free-text tutoring, commit after replies, and no recall on the structured graded-button path.
- Up to three episodes in 900 characters; local 256-dimensional Potion embeddings and Python scoring.
- Short reply excerpt as approach; no LLM-written multi-turn episode summary.
- Unknown outcome for ordinary ungraded turns.
- Assessment status, source label, response excerpt, and grouping key with migration 021.
- Boolean grading validation, attempt-persistence flags, declared proficiency state, and closed-row replay protection.
- Feedback prefers interaction/content matches; concept fallback remains when content is absent.
- Consolidation helpers deduplicate known attempts and require repeated response evidence or verified-source success.

The last helpers are not fully connected: the real database SELECT omits their required fields. Do not equate helper implementation with functioning end-to-end consolidation.

## Schema and projects

| Migration | User-project change |
|---|---|
| 006 | Learner facts and related tables. |
| 009 | Learner-fact embedding array. |
| 017 | Base episodic_memory table, indexes, uniqueness and RLS. |
| 018 | Approach, consolidation links and history table. |
| 020 | Assessment status, source, response, interaction ID and claimed result. |

Follow db/migrations/APPLY.md for prerequisites. Personal memory uses SUPABASE_URL / SUPABASE_KEY and the user project. Shared content uses the separate retrieval project and RETRIEVAL_DATABASE_URL. Restart after schema changes because probes are cached. Migration 021 does not infer evidence for historical rows.

## Commands

```powershell
python -m agents.memory_status
python -m agents.memory_consolidate --user-id <student-user-id> --dry-run
python -m agents.memory_consolidate --user-id <student-user-id>
python -m agents.memory_consolidate --pending
```

Status probes older memory requirements and URL presence; it does not validate migration 021, retrieval connectivity, or scheduling. Dry-run writes nothing but can invoke the LLM with eligible input. API startup does not schedule consolidation.

Before scheduling, address the query-projection, claim finalization/recovery, and unsupported-fact retirement gaps. The current database-fed job normally finds no eligible cluster under the stricter rules.

Read-only SQL after migration 021:

```sql
SELECT episode_id, session_id, assistant_turn_index, concept_id, content_id,
       interaction_id, attempt_id, feedback_attempt_id,
       observed_outcome, feedback_outcome, assessment_status, evidence_source,
       student_response, approach, proficiency_before, proficiency_after,
       status, consolidated_at
FROM public.episodic_memory
WHERE user_id = '<student-user-id>'
ORDER BY created_at DESC
LIMIT 20;
```

## Remaining behavior to verify or repair

- Free-text answers are not automatically assessed on every path.
- Delayed feedback can still run after failed attempt persistence.
- Provenance is not propagated to earlier feedback rows or shown in recall lines.
- Optional query-based learner-fact retrieval retains NumPy boolean checks.
- Final consolidation logging INSERTs instead of updating its claim.
- Unsupported-fact retirement writes NULL to a field where NULL means active.
- Background discovery is bounded without pagination; repeated runs need not advance.

## Verification

```powershell
python -m pytest tests/test_memory_agent.py tests/test_episodic_memory.py tests/test_memory_consolidate.py tests/test_memory_lifecycle.py tests/test_memory_status.py -q
```

These are reproduction commands, not a claim they were run during this documentation refresh. Fake-backed tests do not establish live database projection, constraint, or concurrency behavior.

An acceptance demonstration should separately verify unknown-outcome storage, persisted grading, correct feedback association, provenance, cross-session recall, and consolidation from independent evidence. A successful tutoring response alone does not prove memory persistence.
