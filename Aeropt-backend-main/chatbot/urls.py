"""
chatbot/urls.py
===============
Mounted at: /api/v1/chat/

All routes below are relative to /api/v1/chat/.
"""

from django.urls import path

from chatbot.views import (
    ChatSessionListCreateView,
    ChatSessionDetailView,
    EndSessionView,
    ChatMessageListCreateView,
    ChatMessageStreamView,
    ConceptSnapshotView,
)

urlpatterns = [
    # --- Session endpoints ---
    # GET  /api/v1/chat/sessions/         → list all past sessions (paginated)
    # POST /api/v1/chat/sessions/         → start a new study session
    path("sessions/", ChatSessionListCreateView.as_view(), name="session-list-create"),
    # GET    /api/v1/chat/sessions/{id}/       → session detail + message history
    # DELETE /api/v1/chat/sessions/{id}/       → delete session + all messages
    path("sessions/<uuid:session_id>/", ChatSessionDetailView.as_view(), name="session-detail"),
    # PATCH  /api/v1/chat/sessions/{id}/end/   → end session (sets ended_at, is_active=False)
    path("sessions/<uuid:session_id>/end/", EndSessionView.as_view(), name="session-end"),

    # --- Message endpoints ---
    # GET  /api/v1/chat/sessions/{id}/messages/  → paginated message history
    # POST /api/v1/chat/sessions/{id}/messages/  → send message → LLM → structured response
    path("sessions/<uuid:session_id>/messages/", ChatMessageListCreateView.as_view(), name="message-list-create"),
    # GET  /api/v1/chat/sessions/{id}/messages/{msg_id}/stream/  → SSE token stream
    path(
        "sessions/<uuid:session_id>/messages/<uuid:msg_id>/stream/",
        ChatMessageStreamView.as_view(),
        name="message-stream",
    ),
    # --- Concept snapshot endpoint ---
    # GET  /api/v1/chat/sessions/{id}/concepts/  → aggregated concept snapshots for session
    path("sessions/<uuid:session_id>/concepts/", ConceptSnapshotView.as_view(), name="session-concepts"),
]
