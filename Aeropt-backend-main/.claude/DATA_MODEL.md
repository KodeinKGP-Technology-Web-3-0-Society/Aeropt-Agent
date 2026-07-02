# Prepwise — Data Model

## Entity Relationship Overview

```
User (users.User)
 ├── StudentProfile       [1:1]   exam_target, avatar, active_chapters
 ├── ChatSession          [1:N]   per study session
 │    └── ChatMessage     [1:N]   user + assistant messages
 │         └── ConceptSnapshot [1:N]  concept state at message time
 ├── ConceptProficiency   [1:N]   one row per concept (upserted)
 ├── DailyProficiencySnapshot [1:N]  one row per chapter per day
 ├── StudyStreak          [1:1]   current + longest streak
 ├── PracticeAttempt      [1:N]   one row per question attempted
 └── WeeklyImprovement    [1:N]   recomputed weekly
```

---

## Table Schemas

### users.User  (AUTH_USER_MODEL)
| Field        | Type            | Notes                          |
|--------------|-----------------|--------------------------------|
| id           | UUID / int PK   |                                |
| email        | EmailField      | unique, used as login          |
| username     | CharField       |                                |
| first_name   | CharField       |                                |
| last_name    | CharField       |                                |
| is_verified  | BooleanField    | email verification             |
| created_at   | DateTimeField   | auto_now_add                   |
| updated_at   | DateTimeField   | auto_now                       |

### users.StudentProfile
| Field           | Type                    | Notes                              |
|-----------------|-------------------------|------------------------------------|
| user            | OneToOneField → User    |                                    |
| exam_target     | CharField               | JEE_ADVANCED / JEE_MAINS           |
| avatar_url      | URLField                | profile pic                        |
| target_year     | PositiveSmallIntegerField |                                  |
| active_chapters | JSONField               | list of chapter slugs              |
| onboarding_done | BooleanField            |                                    |
| created_at      | DateTimeField           |                                    |
| updated_at      | DateTimeField           |                                    |

### chatbot.ChatSession
| Field              | Type              | Notes                               |
|--------------------|-------------------|-------------------------------------|
| id                 | UUID PK           |                                     |
| user               | FK → User         |                                     |
| title              | CharField         | auto-generated from first message   |
| subject            | CharField         | e.g. "Physics"                      |
| active_chapters    | JSONField         | chapter slugs in scope              |
| started_at         | DateTimeField     |                                     |
| ended_at           | DateTimeField     | null until ended                    |
| is_active          | BooleanField      | one active session per user         |
| system_prompt_used | TextField         | LLM system prompt (audit)           |

### chatbot.ChatMessage
| Field               | Type              | Notes                               |
|---------------------|-------------------|-------------------------------------|
| id                  | UUID PK           |                                     |
| session             | FK → ChatSession  |                                     |
| role                | CharField         | user / assistant                    |
| content             | TextField         | raw text + KaTeX                    |
| structured_response | JSONField         | null for user messages              |
| tokens_used         | PositiveIntegerField |                                  |
| latency_ms          | PositiveIntegerField |                                  |
| created_at          | DateTimeField     |                                     |

### chatbot.ConceptSnapshot
| Field          | Type              | Notes                   |
|----------------|-------------------|-------------------------|
| id             | int PK            |                         |
| message        | FK → ChatMessage  |                         |
| concept_name   | CharField         |                         |
| score_at_time  | PositiveSmallIntegerField | 0–100           |
| trend          | CharField         | up / down / flat        |
| is_prerequisite| BooleanField      |                         |

### analytics.ConceptProficiency
| Field           | Type              | Notes                              |
|-----------------|-------------------|------------------------------------|
| id              | int PK            |                                    |
| user            | FK → User         |                                    |
| concept_name    | CharField         |                                    |
| chapter         | CharField         |                                    |
| score           | PositiveSmallIntegerField | 0–100                    |
| attempts        | PositiveIntegerField |                               |
| correct_attempts| PositiveIntegerField |                               |
| last_attempted  | DateTimeField     |                                    |
| trend           | CharField         | up / down / flat                   |
| unique_together | (user, concept_name) |                               |

### analytics.DailyProficiencySnapshot
| Field     | Type          | Notes                          |
|-----------|---------------|--------------------------------|
| user      | FK → User     |                                |
| chapter   | CharField     |                                |
| date      | DateField     |                                |
| avg_score | FloatField    | mean concept score for chapter |
| unique_together | (user, chapter, date) |              |

### analytics.StudyStreak
| Field          | Type             | Notes                     |
|----------------|------------------|---------------------------|
| user           | OneToOneField    |                           |
| current_streak | PositiveIntegerField |                       |
| longest_streak | PositiveIntegerField |                       |
| last_study_date| DateField        |                           |

### analytics.PracticeAttempt
| Field           | Type              | Notes                              |
|-----------------|-------------------|------------------------------------|
| id              | UUID PK           |                                    |
| user            | FK → User         |                                    |
| concept_name    | CharField         |                                    |
| chapter         | CharField         |                                    |
| question_id     | CharField         | stable question identifier         |
| question_type   | CharField         | SCQ / MCQ / Numerical / PYQ        |
| difficulty      | PositiveSmallIntegerField | 1–5                      |
| selected_answer | CharField         |                                    |
| is_correct      | BooleanField      |                                    |
| time_taken_secs | PositiveIntegerField |                               |
| mistake_type    | CharField         | Conceptual / Procedural / Calculation / Misinterpretation (null if correct) |
| attempted_at    | DateTimeField     |                                    |

### analytics.WeeklyImprovement
| Field        | Type          | Notes                           |
|--------------|---------------|---------------------------------|
| user         | FK → User     |                                 |
| concept_name | CharField     |                                 |
| score_7d_ago | PositiveSmallIntegerField |                   |
| score_now    | PositiveSmallIntegerField |                   |
| delta        | IntegerField  | score_now − score_7d_ago        |
| week_start   | DateField     |                                 |
