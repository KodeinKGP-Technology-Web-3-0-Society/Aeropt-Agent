"""
APP: apis
=========
Central API configuration layer — no domain models live here.

This app is the DRF (Django REST Framework) hub. It owns:
  - Global DRF settings (auth classes, permission classes, throttling, pagination)
  - Top-level URL router wiring all app routers under /api/v1/
  - API versioning strategy (URL-based: /api/v1/)
  - Shared serializer mixins, pagination classes, response envelope helpers
  - Custom exception handler — normalises all DRF errors to:
      { "success": false, "data": null, "errors": [{"field": "...", "message": "..."}] }
  - Health-check endpoint

NO MODELS — this app has no database tables.

ENDPOINTS OWNED BY THIS APP
----------------------------
GET  /api/v1/health/
  — Returns {"status": "ok", "version": "1.0.0"}
  — No auth required; used by load balancer / uptime monitoring

URL ROUTING PLAN (prepwise/urls.py will delegate to each app)
--------------------------------------------------------------
/api/v1/auth/         → users.urls      (AuthViewSet)
/api/v1/users/        → users.urls      (UserProfileViewSet)
/api/v1/chat/         → chatbot.urls    (ChatSessionViewSet, ChatMessageViewSet)
/api/v1/analytics/    → analytics.urls  (Dashboard, Progress, Concepts, Attempts, Streak)
/api/schema/          → drf-spectacular (auto-generated OpenAPI 3 + Swagger UI)

SHARED UTILITIES TO ADD (apis/)
---------------------------------
apis/utils.py
  - success_response(data, status=200)  → standard success envelope
  - error_response(errors, status=400)  → standard error envelope
  - custom_exception_handler(exc, ctx)  → registered in DRF EXCEPTION_HANDLER setting

apis/pagination.py
  - StandardResultsPagination  — page_size=20, max_page_size=100

apis/permissions.py
  - IsOwner  — object-level permission: user can only access their own resources

THIRD-PARTY PACKAGES TO INSTALL  (requirements.txt)
----------------------------------------------------
djangorestframework           — core DRF
djangorestframework-simplejwt — JWT auth (access + refresh tokens)
django-cors-headers           — CORS for frontend (React / Next.js)
django-filter                 — query-param filtering on list views
drf-spectacular               — OpenAPI 3 schema + Swagger UI
celery                        — async LLM calls + scheduled analytics jobs
redis                         — Celery broker + Django cache backend
anthropic                     — Anthropic Python SDK for direct LLM calls
python-dotenv                 — .env file management
psycopg2-binary               — PostgreSQL driver (replace SQLite in production)
"""

from django.db import models

# No models — this app is configuration-only.
