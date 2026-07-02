"""
VIEWS — users app
=================
No implementation yet. See models.py for the full schema and endpoint list.

Planned ViewSets / APIViews (Django REST Framework):

AuthViewSet
  - register()         POST /api/v1/auth/register/
  - login()            POST /api/v1/auth/login/
  - logout()           POST /api/v1/auth/logout/
  - token_refresh()    POST /api/v1/auth/token/refresh/
  - verify_email()     POST /api/v1/auth/verify-email/
  - forgot_password()  POST /api/v1/auth/forgot-password/
  - reset_password()   POST /api/v1/auth/reset-password/

UserProfileViewSet
  - retrieve()         GET    /api/v1/users/me/
  - partial_update()   PATCH  /api/v1/users/me/
  - destroy()          DELETE /api/v1/users/me/
  - update_chapters()  PATCH  /api/v1/users/me/chapters/

Permissions:
  - Public: register, login, forgot-password, reset-password, verify-email
  - IsAuthenticated: everything else
"""

from django.shortcuts import render

# Views will be implemented here.
