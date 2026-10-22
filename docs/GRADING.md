# Grading contract

One result describes three different facts:

| Field | Values | Meaning |
|---|---|---|
| `grade_status` | `correct`, `incorrect`, `self_reported`, `ungradable`, `invalid_submission`, `error` | What the submission means |
| `evidence_source` | `answer_key`, `student_self_report`, `model_unverified`, `none` | Why that status was chosen |
| `persistence_status` | `committed`, `failed`, `not_attempted`, `unknown` | Whether progress was saved |

`graded: true` means an answer key produced `correct` or `incorrect`. `recorded: true` means the attempt row committed. A self-report can be recorded and still have `graded: false`. An unknown grade never sets `correct: false`.

`proficiency_before`, `proficiency_after`, and `proficiency_pct` are present only when `persistence_status` is `committed`. Scores are stored from 0 to 1. The panel shows 0–100. A cold-start row stays unmeasured (`measured: false`) until an attempt commits. A stored 0 stays 0.

`persistence_status: unknown` means a timeout left the commit uncertain. Retry the same `submission_id`. Do not mint a new id.

## Who may write progress

- Answer key, status `correct` or `incorrect`: the served question's structured key, or one unambiguous legacy key only when structured data is absent. A malformed structured key does not fall back to solution text.
- Explicit Correct/Incorrect button, status `self_reported`: only while that question instance is still the pending one. The button keeps the existing proficiency formula and is labeled as the student's own check. It is not upgraded to a key check.
- A model `is_correct` never selects `student_self_report`. With a key, the key wins. Without a key, nothing is written.

Invalid, ungradable, and model-unverified submissions do not change proficiency, streaks, difficulty, or error counts. A missing error type is stored as `unknown`, not `conceptual`.

## Idempotency

`submission_id` is chosen when the question is shown (`question_instance_id`) unless the client sends one. Retries and double-clicks reuse it. A new attempt uses a new id. The same id with a different answer returns `submission_conflict` and does not write. A retry after the question is closed returns the original committed row when the payload matches.

Migration `db/migrations/022_grading_commit.sql` is required. The service does not fall back to separate inserts when `commit_graded_attempt` is missing.

## Scoring that this release keeps

`new = 0.7 * old + 0.3 * outcome`. A correct answer slower than twice the measured timed average uses outcome `0.7`, which can lower a score above 0.70. The timed average counts timed attempts only. Missing or negative times do not change it and do not dampen. Historical averages were not assigned a fake sample count: `timed_attempts` starts at 0, and the next valid time starts a new series.

Readiness stays on the existing 40/20/20/20 formula. With no committed attempt and no completed graded session it returns `score: null` and `confidence: 0`.

## Caller examples

Study answer, keyed and saved:

```json
{"graded": true, "recorded": true, "grade_status": "correct", "evidence_source": "answer_key", "persistence_status": "committed", "correct": true, "proficiency_pct": 65}
```

Study answer, checked, storage unavailable:

```json
{"graded": true, "recorded": false, "grade_status": "incorrect", "evidence_source": "answer_key", "persistence_status": "failed", "failure_reason": "persistence_unavailable", "correct": false}
```

Chat button:

```json
{"graded": false, "recorded": true, "grade_status": "self_reported", "evidence_source": "student_self_report", "persistence_status": "committed", "self_report_correct": true, "proficiency_pct": 65}
```

Onboarding must send `answer`. `is_correct` is ignored. `overall` averages only concepts with a committed attempt.

Classroom assessment grades the served item's key. A long solution with no key is `ungradable`, not incorrect. The assess chat surface cannot call `record_attempt` again while explaining that result.

Voice transcription and synthesis return 503 until `VOICE_SPENDING_ENABLED=true`. The speaker list stays public. When spending is enabled, those two routes require the learner token and reject uploads over `MAX_VOICE_BYTES`.
