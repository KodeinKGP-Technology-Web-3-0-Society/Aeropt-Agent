"""
APP: chatbot
============
Powers the Study Session page — the core LLM-driven tutoring interface.

This app manages a DIRECT connection to an LLM provider (Anthropic Claude
recommended — strong at physics reasoning + supports structured JSON output via
tool_use). It stores every session and every message so that:
  - Users can resume a past session with full history intact.
  - The right-panel "Concepts in this response" UI is populated from structured
    LLM output (concept names + proficiency bars + trend arrows).
  - The "Prerequisite chain" shows concept dependencies for each exchange.
  - The analytics app can mine which concepts appeared in each exchange.

From the Figma Study Session screen:
  - User types a physics question; KaTeX math is supported ($F = ma$).
  - LLM reply contains:
      • FILLING A GAP FIRST — prerequisite bridge before the solution
      • Numbered SOLUTION steps
      • Highlighted INSIGHT callout box
      • "Try next —" follow-up suggestion chips
  - Right panel: concept name, proficiency bar (%), trend arrow (↑/↓).
  - Prerequisite chain: ordered list of concepts with health-color dots.

TABLES PLANNED
--------------
1. ChatSession
   - user               : FK → users.User
   - title              : CharField — auto-generated from first message
                          (e.g. "Torque pivot problem")
   - subject            : CharField (e.g. "Physics")
   - active_chapters    : JSONField — chapter slugs in scope for this session
                          (copied from StudentProfile at session start)
   - started_at         : DateTimeField (auto_now_add)
   - ended_at           : DateTimeField — null until user clicks "End Session"
   - is_active          : BooleanField — only one active session per user at a time
   - system_prompt_used : TextField — exact system prompt sent to LLM (audit trail)

2. ChatMessage
   - session            : FK → ChatSession (related_name='messages')
   - role               : CharField, choices = ['user', 'assistant']
   - content            : TextField — raw text with inline KaTeX ($...$) preserved
   - structured_response: JSONField — null for user messages; for assistant messages:
                          {
                            "filling_gap": "...",
                            "solution_steps": ["step 1 ...", "step 2 ..."],
                            "insight": "...",
                            "try_next": ["What if F=20N?", "How does torque relate to inertia?"],
                            "concepts_mentioned": ["Torque", "Newton's 2nd Law"],
                            "prerequisite_chain": [
                              {"concept": "Torque",            "score": 34, "color": "orange"},
                              {"concept": "Newton's 2nd Law",  "score": 80, "color": "green"},
                              {"concept": "Free Body Diagram", "score": 71, "color": "green"}
                            ]
                          }
   - tokens_used        : PositiveIntegerField — LLM input+output tokens (cost tracking)
   - latency_ms         : PositiveIntegerField — wall-clock LLM response time
   - created_at         : DateTimeField (auto_now_add)

3. ConceptSnapshot  (per-message concept state — feeds the right-panel UI)
   - message            : FK → ChatMessage
   - concept_name       : CharField
   - score_at_time      : PositiveSmallIntegerField (0–100)
   - trend              : CharField, choices = ['up', 'down', 'flat']
   - is_prerequisite    : BooleanField — whether it appears in the prerequisite chain

API ENDPOINTS NEEDED
--------------------
POST   /api/v1/chat/sessions/
  — Start a new study session

GET    /api/v1/chat/sessions/
  — List all past sessions for the user (paginated, newest first)

GET    /api/v1/chat/sessions/{session_id}/
  — Session detail + full message history (for restoring a past session)

PATCH  /api/v1/chat/sessions/{session_id}/end/
  — End the session (sets ended_at, is_active=False)

DELETE /api/v1/chat/sessions/{session_id}/
  — Hard-delete a session and all its messages

POST   /api/v1/chat/sessions/{session_id}/messages/
  — HOT PATH: send a user message
    1. Save ChatMessage(role='user')
    2. Fetch full history + student concept scores from analytics app
    3. Build system prompt injecting weak concepts + proficiency scores
    4. Call Anthropic API with tool_use for structured JSON output
    5. Parse → save ChatMessage(role='assistant') + ConceptSnapshot rows
    6. Fire signal → analytics app updates ConceptProficiency scores
    7. Return serialized assistant message
  — Non-streaming path; see /stream/ for token-by-token SSE

GET    /api/v1/chat/sessions/{session_id}/messages/
  — Paginated message history

GET    /api/v1/chat/sessions/{session_id}/messages/{msg_id}/stream/
  — Server-Sent Events: streams assistant reply token by token
    Saves the full ChatMessage + ConceptSnapshot on stream completion

GET    /api/v1/chat/sessions/{session_id}/concepts/
  — Aggregated ConceptSnapshot list for the session (session-end summary card)

NOTES ON LLM INTEGRATION
--------------------------
- Provider     : Anthropic Claude (claude-sonnet-4-6 or claude-opus-4-8)
- Auth         : ANTHROPIC_API_KEY loaded from .env
- Structured   : Use tool_use / function calling — LLM returns validated JSON,
                 no fragile string parsing
- System prompt: Inject student's weak concepts + proficiency scores from analytics
                 so LLM tailors difficulty and fills prerequisite gaps automatically
- KaTeX        : Backend passes $...$ and $$...$$ through untouched; frontend renders
- Streaming    : SSE stream → save full message on stream end
- Rate limit   : 50 messages / hour per user (configurable via DRF throttle)
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_sessions")
    title = models.CharField(max_length=200, default="New Session")
    subject = models.CharField(max_length=50, default="Physics")
    active_chapters = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    system_prompt_used = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user",),
                condition=Q(is_active=True),
                name="unique_active_chat_session_per_user",
            )
        ]

    def end(self):
        if self.is_active:
            self.is_active = False
            self.ended_at = timezone.now()
            self.save(update_fields=("is_active", "ended_at"))

    def __str__(self):
        return f"{self.user_id}:{self.title}"


class ChatMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = (
        (ROLE_USER, "User"),
        (ROLE_ASSISTANT, "Assistant"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    structured_response = models.JSONField(null=True, blank=True)
    tokens_used = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.session_id}:{self.role}:{self.created_at.isoformat()}"


class ConceptSnapshot(models.Model):
    TREND_UP = "up"
    TREND_DOWN = "down"
    TREND_FLAT = "flat"
    TREND_CHOICES = (
        (TREND_UP, "Up"),
        (TREND_DOWN, "Down"),
        (TREND_FLAT, "Flat"),
    )

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="concept_snapshots")
    concept_name = models.CharField(max_length=200)
    score_at_time = models.PositiveSmallIntegerField(default=0)
    trend = models.CharField(max_length=10, choices=TREND_CHOICES, default=TREND_FLAT)
    is_prerequisite = models.BooleanField(default=False)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return f"{self.message_id}:{self.concept_name}"
