# Prepwise — Full API Plan

Base URL: `/api/v1/`

---

## users app

| Method | Endpoint                    | Description                              | Auth     |
|--------|-----------------------------|------------------------------------------|----------|
| POST   | /auth/register/             | Create account (email + password)        | Public   |
| POST   | /auth/login/                | Returns JWT access + refresh tokens      | Public   |
| POST   | /auth/logout/               | Blacklist refresh token                  | Required |
| POST   | /auth/token/refresh/        | Get new access token                     | Public   |
| POST   | /auth/verify-email/         | Verify email with OTP or signed link     | Public   |
| POST   | /auth/forgot-password/      | Send password-reset link                 | Public   |
| POST   | /auth/reset-password/       | Set new password using reset token       | Public   |
| GET    | /users/me/                  | Fetch logged-in user + profile           | Required |
| PATCH  | /users/me/                  | Update name, avatar, target year         | Required |
| PATCH  | /users/me/chapters/         | Update active chapter list               | Required |
| DELETE | /users/me/                  | Soft-delete account                      | Required |

---

## chatbot app

| Method | Endpoint                                          | Description                                      | Auth     |
|--------|---------------------------------------------------|--------------------------------------------------|----------|
| POST   | /chat/sessions/                                   | Start a new study session                        | Required |
| GET    | /chat/sessions/                                   | List all past sessions (paginated)               | Required |
| GET    | /chat/sessions/{id}/                              | Session detail + full message history            | Required |
| PATCH  | /chat/sessions/{id}/end/                          | End session                                      | Required |
| DELETE | /chat/sessions/{id}/                              | Delete session + messages                        | Required |
| POST   | /chat/sessions/{id}/messages/                     | Send message → LLM → structured response         | Required |
| GET    | /chat/sessions/{id}/messages/                     | Paginated message history                        | Required |
| GET    | /chat/sessions/{id}/messages/{msg_id}/stream/     | SSE token stream of assistant reply              | Required |
| GET    | /chat/sessions/{id}/concepts/                     | Aggregated concept snapshots for session         | Required |

### POST /chat/sessions/{id}/messages/ — Response shape
```json
{
  "id": "uuid",
  "role": "assistant",
  "content": "Torque is defined as $\\tau = r \\times F$ ...",
  "structured_response": {
    "filling_gap": "Before torque, recall Newton's 2nd Law ...",
    "solution_steps": ["Step 1 ...", "Step 2 ...", "Step 3 ..."],
    "insight": "The mass (2kg) is a distractor here — torque depends only on force and lever arm.",
    "try_next": ["What if the force was applied at a 30° angle?", "How does torque relate to moment of inertia?"],
    "concepts_mentioned": ["Torque", "Newton's 2nd Law"],
    "prerequisite_chain": [
      {"concept": "Torque",            "score": 34, "color": "orange"},
      {"concept": "Newton's 2nd Law",  "score": 80, "color": "green"},
      {"concept": "Free Body Diagram", "score": 71, "color": "green"}
    ]
  },
  "tokens_used": 842,
  "latency_ms": 1240,
  "created_at": "2025-02-05T10:23:00Z"
}
```

---

## analytics app

| Method | Endpoint                         | Description                                       | Auth     |
|--------|----------------------------------|---------------------------------------------------|----------|
| GET    | /analytics/dashboard/            | Full Dashboard page payload (cached 5 min)        | Required |
| GET    | /analytics/progress/             | Full Progress page payload (cached 15 min)        | Required |
| GET    | /analytics/concepts/             | All ConceptProficiency rows for user              | Required |
| POST   | /analytics/practice-attempts/    | Log a practice attempt + update scores            | Required |
| GET    | /analytics/streak/               | Current and longest study streak                  | Required |

### GET /analytics/dashboard/ — Response shape
```json
{
  "overall_proficiency": 64,
  "total_concepts_attempted": 18,
  "weak_concepts": [
    {"name": "Torque", "score": 34},
    {"name": "Constraint Motion", "score": 34},
    {"name": "Rolling with Slipping", "score": 38}
  ],
  "session_streak": {
    "current": 4,
    "longest": 7,
    "dots": [true, true, true, true, false, false, false]
  },
  "chapters": [
    {
      "name": "Laws of Motion",
      "rollup_score": 72,
      "concepts": [
        {"name": "Newton's First Law", "score": 82, "attempts": 14, "is_weak": false},
        {"name": "Free Body Diagram",  "score": 71, "attempts": 11, "is_weak": false},
        {"name": "Constraint Motion",  "score": 34, "attempts": 6,  "is_weak": true}
      ]
    }
  ]
}
```

### GET /analytics/progress/ — Response shape
```json
{
  "total_attempts": 147,
  "overall_accuracy": 71,
  "most_improved": {"concept": "Work-Energy Theorem", "delta": 22},
  "proficiency_over_time": [
    {"date": "2025-02-01", "Laws of Motion": 45, "Work Energy and Power": 38, "Rotational Motion": 22}
  ],
  "mistake_breakdown": {
    "Conceptual":        {"count": 14, "pct": 34},
    "Procedural":        {"count": 12, "pct": 28},
    "Calculation":       {"count": 9,  "pct": 22},
    "Misinterpretation": {"count": 7,  "pct": 16}
  },
  "concept_breakdown": [
    {
      "concept": "Torque", "chapter": "Rotational Motion",
      "score": 32, "attempts": 18, "accuracy": 33,
      "last_attempted": "2h ago", "trend_data": [28, 29, 30, 31, 32]
    }
  ]
}
```

---

## apis app

| Method | Endpoint       | Description         | Auth   |
|--------|----------------|---------------------|--------|
| GET    | /health/       | Service health check | Public |
| GET    | /schema/       | OpenAPI 3 schema     | Public |
| GET    | /schema/swagger-ui/ | Swagger UI      | Public |
