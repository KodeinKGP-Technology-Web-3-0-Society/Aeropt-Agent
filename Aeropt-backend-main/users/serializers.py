"""
users/serializers.py
====================
Serializers for the users app.

PLANNED SERIALIZERS
-------------------

RegisterSerializer
  - Fields: email, password, confirm_password, first_name, last_name
  - Validates: passwords match, email is unique
  - Creates: User + StudentProfile (defaults: onboarding_done=False)

LoginSerializer
  - Fields: email, password
  - Validates credentials, returns JWT access + refresh tokens

TokenRefreshSerializer
  - Fields: refresh
  - Returns: new access token (wraps simplejwt)

UserSerializer  (read-only profile output)
  - Fields: id, email, first_name, last_name, is_verified, created_at
  - Nested: StudentProfileSerializer

StudentProfileSerializer
  - Fields: exam_target, avatar_url, target_year, active_chapters, onboarding_done

UpdateProfileSerializer  (PATCH /users/me/)
  - Fields: first_name, last_name, avatar_url, target_year, exam_target
  - All optional

UpdateChaptersSerializer  (PATCH /users/me/chapters/)
  - Fields: active_chapters (list of chapter slugs)

ForgotPasswordSerializer
  - Fields: email

ResetPasswordSerializer
  - Fields: token, new_password, confirm_password
"""

# Serializers will be implemented here.
