# Prepwise — Project Context

## What is Prepwise?

An AI-powered JEE exam prep platform. Students (JEE Advanced / JEE Mains) study physics
concepts through:
1. **Study Session** — LLM-powered chat tutor (direct Anthropic Claude connection)
2. **Practice** — Filtered question bank (SCQ, MCQ, Numerical, PYQ) with solutions
3. **Dashboard** — Live concept health, weak concept flags, session streak
4. **Progress** — 14-day trajectory, mistake breakdown, concept table with sparklines

## Tech Stack

| Layer        | Choice                                          |
|--------------|-------------------------------------------------|
| Backend      | Django 6 + Django REST Framework                |
| Auth         | JWT via djangorestframework-simplejwt            |
| LLM          | Anthropic Claude (claude-sonnet-4-6 or opus-4-8)|
| Async tasks  | Celery + Redis                                  |
| Cache        | Redis                                           |
| DB (dev)     | SQLite (switch to PostgreSQL in production)     |
| API docs     | drf-spectacular (OpenAPI 3 + Swagger UI)        |

## App Responsibilities

| App         | Owns                                                        |
|-------------|-------------------------------------------------------------|
| `users`     | Custom User model, StudentProfile, JWT auth endpoints       |
| `chatbot`   | ChatSession, ChatMessage, ConceptSnapshot, LLM integration  |
| `analytics` | ConceptProficiency, StudyStreak, PracticeAttempt, snapshots |
| `apis`      | DRF config, routing, shared utils, health-check, no models  |

## Key Design Decisions

- `AUTH_USER_MODEL = 'users.User'` — must be set before first migration
- LLM responses use `tool_use` (function calling) to return structured JSON — no parsing hacks
- Analytics updates are triggered by a Django signal fired from the chatbot app
- Dashboard and Progress endpoints are cached in Redis (TTL 5min / 15min)
- KaTeX math ($...$) is passed through backend untouched; rendered by frontend
- Streaming chat replies use Server-Sent Events (SSE)

## Domain Vocabulary

| Term                  | Meaning                                                   |
|-----------------------|-----------------------------------------------------------|
| Concept               | A discrete physics topic (e.g. "Torque", "Free Body Diagram") |
| Chapter               | A grouping of concepts (e.g. "Laws of Motion")            |
| Proficiency Score     | 0–100 per concept; <50 = WEAK (red), 50–69 = yellow, ≥70 = green |
| Session               | One continuous LLM chat session; can be ended and resumed |
| Practice Attempt      | A single question answered in the Practice page           |
| Mistake Type          | Conceptual / Procedural / Calculation / Misinterpretation |
| Prerequisite Chain    | Ordered dependency graph for a concept shown in chat UI   |
| Study Streak          | Consecutive days with at least one chat message or attempt|
