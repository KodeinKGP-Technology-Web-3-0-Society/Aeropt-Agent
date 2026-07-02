"""
APP: users
==========
Handles authentication, student profiles, and exam configuration.

Every learner on Prepwise is a student preparing for JEE (Advanced or Mains).
This app owns the custom User model and the richer StudentProfile that stores
exam target, avatar, and chapter preferences — visible in the sidebar bottom-left
(e.g., "Arjun Sharma · JEE Advanced") and the dashboard greeting ("Good morning, Arjun").

TABLES PLANNED
--------------
1. User  (extends AbstractUser — set AUTH_USER_MODEL = 'users.User' in settings)
   - email           : EmailField, unique — used as login identifier
   - username        : CharField (auto-generated or user-set)
   - first_name      : CharField
   - last_name       : CharField
   - is_verified     : BooleanField — email-verified flag
   - created_at      : DateTimeField (auto_now_add)
   - updated_at      : DateTimeField (auto_now)

2. StudentProfile  (OneToOne → User)
   - exam_target       : CharField, choices = ['JEE_ADVANCED', 'JEE_MAINS']
                         shown as a badge in the sidebar (e.g., "JEE Advanced")
   - avatar_url        : URLField / ImageField — profile pic in sidebar
   - target_year       : PositiveSmallIntegerField (e.g. 2025, 2026)
   - active_chapters   : JSONField — list of chapter slugs the student has enabled
                         (e.g. ["laws_of_motion", "work_energy", "rotational_motion"])
                         drives the chapter filter tabs in Practice and the
                         "Physics — Concept Health" section on the Dashboard
   - onboarding_done   : BooleanField — redirect to onboarding flow if False
   - created_at        : DateTimeField (auto_now_add)
   - updated_at        : DateTimeField (auto_now)

API ENDPOINTS NEEDED
--------------------
POST   /api/v1/auth/register/          — create account (email + password)
POST   /api/v1/auth/login/             — returns JWT access + refresh tokens
POST   /api/v1/auth/logout/            — blacklist the refresh token
POST   /api/v1/auth/token/refresh/     — get new access token from refresh token
POST   /api/v1/auth/verify-email/      — verify email with OTP / signed link
POST   /api/v1/auth/forgot-password/   — send password-reset link to email
POST   /api/v1/auth/reset-password/    — set new password using reset token

GET    /api/v1/users/me/               — fetch logged-in user + profile
PATCH  /api/v1/users/me/               — update name, avatar, target year, exam target
PATCH  /api/v1/users/me/chapters/      — update which chapters are active
DELETE /api/v1/users/me/               — soft-delete account

NOTES
-----
- Use djangorestframework-simplejwt for JWT auth.
- All endpoints except register / login / forgot-password require IsAuthenticated.
- Standard response envelope: { "success": bool, "data": {}, "errors": [] }
- AUTH_USER_MODEL = 'users.User' must be set in settings.py before first migration.
"""

from django.db import models

# Models will be implemented here — see docstring above for schema.
