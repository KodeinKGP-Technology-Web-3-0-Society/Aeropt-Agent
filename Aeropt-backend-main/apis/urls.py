"""
apis/urls.py — Central API Router
==================================
This is the single entry point for ALL /api/v1/ routes.
prepwise/urls.py delegates everything under /api/v1/ to this file.

Routing structure:
  /api/v1/health/         → HealthCheckView (defined in apis/views.py)
  /api/v1/auth/           → users.urls      (register, login, logout, token refresh, verify-email, etc.)
  /api/v1/users/          → users.urls      (/me/ profile endpoints)
  /api/v1/chat/           → chatbot.urls    (sessions, messages, streaming, concept snapshots)
  /api/v1/analytics/      → analytics.urls  (dashboard, progress, concepts, practice-attempts, streak)
  /api/schema/            → drf-spectacular (OpenAPI 3 schema)
  /api/schema/swagger-ui/ → drf-spectacular (Swagger UI)
"""

from django.urls import path, include

# from apis.views import HealthCheckView
# from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    # Health check — no auth required, used by load balancer / uptime monitoring
    # path('health/', HealthCheckView.as_view(), name='health-check'),

    # users app — auth + profile
    # path('auth/', include('users.urls')),
    # path('users/', include('users.urls')),

    # chatbot app — study sessions + LLM messaging
    path('chat/', include('chatbot.urls')),

    # analytics app — dashboard, progress, concept scores
    path('analytics/', include('analytics.urls')),

    # OpenAPI schema + Swagger UI (drf-spectacular)
    # path('schema/', SpectacularAPIView.as_view(), name='schema'),
    # path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
