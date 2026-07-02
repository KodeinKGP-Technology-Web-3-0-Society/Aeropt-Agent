# Prepwise — Backend

AI-powered JEE exam prep platform. Students study physics concepts through an LLM-driven chat tutor, a practice question bank, and a live analytics dashboard.

## Project Structure

```
Aeropt-backend/
├── prepwise/          # Django project config (settings, urls, wsgi, asgi)
├── users/             # Auth + student profiles
├── chatbot/           # LLM study sessions + message history
├── analytics/         # Dashboard + progress data
├── apis/              # DRF config hub + central router
├── requirements.txt
├── .env.example
└── .claude/           # Architecture docs, API plan, data model, implementation order
```

## Apps

| App | Responsibility |
|-----|---------------|
| `users` | Custom User model, StudentProfile (exam target, chapters), JWT auth |
| `chatbot` | ChatSession, ChatMessage, ConceptSnapshot, direct Anthropic LLM connection |
| `analytics` | ConceptProficiency, StudyStreak, PracticeAttempt, dashboard/progress aggregation |
| `apis` | DRF settings, central URL router, shared serializers/utils, health-check |

## Setup

```bash
# 1. Clone and enter the project
cd Aeropt-backend

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and fill in SECRET_KEY, ANTHROPIC_API_KEY, DB credentials, etc.

# 5. Run migrations
python manage.py migrate

# 6. Start the development server
python manage.py runserver
```

## API

All endpoints are versioned under `/api/v1/`. See [.claude/API_PLAN.md](.claude/API_PLAN.md) for the full endpoint reference.

| Prefix | App |
|--------|-----|
| `/api/v1/auth/` | users — register, login, logout, token refresh |
| `/api/v1/users/` | users — profile, chapters |
| `/api/v1/chat/` | chatbot — sessions, messages, streaming |
| `/api/v1/analytics/` | analytics — dashboard, progress, concepts, streaks |

## Tech Stack

- **Django 5.1** + **Django REST Framework**
- **JWT auth** via `djangorestframework-simplejwt`
- **LLM** — Anthropic Claude (`anthropic` SDK, direct connection)
- **Async tasks** — Celery + Redis
- **API docs** — drf-spectacular (Swagger UI at `/api/docs/`)
- **DB** — SQLite (dev) → PostgreSQL (production)

## Docs

| File | Contents |
|------|----------|
| [.claude/PROJECT_CONTEXT.md](.claude/PROJECT_CONTEXT.md) | Tech stack, app responsibilities, domain vocabulary |
| [.claude/API_PLAN.md](.claude/API_PLAN.md) | Full endpoint table + request/response shapes |
| [.claude/DATA_MODEL.md](.claude/DATA_MODEL.md) | All table schemas + ER overview |
| [.claude/IMPLEMENTATION_ORDER.md](.claude/IMPLEMENTATION_ORDER.md) | 6-phase build plan |
