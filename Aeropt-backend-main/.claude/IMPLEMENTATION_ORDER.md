# Prepwise — Recommended Implementation Order

## Phase 1 — Foundation
1. Add `requirements.txt` with all third-party packages
2. Set `AUTH_USER_MODEL = 'users.User'` in `settings.py`
3. Implement `users.User` model + `users.StudentProfile`
4. Implement JWT auth endpoints (register, login, logout, token refresh)
5. Run initial migrations

## Phase 2 — Core API Infrastructure
6. Wire up DRF settings in `settings.py` (auth classes, throttling, pagination, exception handler)
7. Implement shared utils in `apis/utils.py` (response envelope, exception handler)
8. Implement `apis/pagination.py` + `apis/permissions.py`
9. Implement `HealthCheckView` + `/api/v1/health/`
10. Set up drf-spectacular schema endpoint

## Phase 3 — Analytics (needed by chatbot for LLM system prompt)
11. Implement `ConceptProficiency` model + migrations
12. Implement `StudyStreak` model
13. Implement `PracticeAttempt` model
14. Implement `DailyProficiencySnapshot` + `WeeklyImprovement` models
15. Implement `GET /api/v1/analytics/concepts/` (chatbot reads this to build system prompt)
16. Implement `POST /api/v1/analytics/practice-attempts/`
17. Implement `GET /api/v1/analytics/dashboard/`
18. Implement `GET /api/v1/analytics/progress/`
19. Add Redis caching to dashboard + progress views

## Phase 4 — Chatbot (LLM core)
20. Implement `ChatSession`, `ChatMessage`, `ConceptSnapshot` models
21. Implement session CRUD endpoints
22. Implement `POST /messages/` — the hot path:
    a. Build system prompt with concept scores from analytics
    b. Call Anthropic API with `tool_use` for structured JSON
    c. Save message + snapshots
    d. Fire signal → analytics updates scores
23. Implement SSE streaming endpoint
24. Wire Django signal: `concept_mentioned` → `analytics.signals.on_concept_mentioned`

## Phase 5 — Scheduled Tasks (Celery)
25. Set up Celery + Redis broker
26. Scheduled task: write `DailyProficiencySnapshot` rows at midnight UTC
27. Scheduled task: recompute `WeeklyImprovement` every Sunday
28. Scheduled task: invalidate stale caches

## Phase 6 — Polish
29. Add `.env` + `python-dotenv` setup
30. Swap SQLite → PostgreSQL config in settings
31. Add CORS config (django-cors-headers) for frontend origin
32. Full OpenAPI schema review + Swagger UI
33. Write tests for each app
