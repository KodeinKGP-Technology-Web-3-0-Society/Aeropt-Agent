"""
users/urls.py
=============
Mounted at:
  /api/v1/auth/   — authentication endpoints
  /api/v1/users/  — profile endpoints

All routes below are relative to their mount prefix.
"""

from django.urls import path

# from users.views import (
#     RegisterView,
#     LoginView,
#     LogoutView,
#     TokenRefreshView,
#     VerifyEmailView,
#     ForgotPasswordView,
#     ResetPasswordView,
#     UserProfileView,
#     UpdateChaptersView,
# )

urlpatterns = [
    # --- Auth endpoints (mounted under /api/v1/auth/) ---
    # path('register/',        RegisterView.as_view(),       name='auth-register'),
    # path('login/',           LoginView.as_view(),          name='auth-login'),
    # path('logout/',          LogoutView.as_view(),         name='auth-logout'),
    # path('token/refresh/',   TokenRefreshView.as_view(),   name='token-refresh'),
    # path('verify-email/',    VerifyEmailView.as_view(),    name='verify-email'),
    # path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    # path('reset-password/',  ResetPasswordView.as_view(),  name='reset-password'),

    # --- Profile endpoints (mounted under /api/v1/users/) ---
    # path('me/',              UserProfileView.as_view(),    name='user-me'),
    # path('me/chapters/',     UpdateChaptersView.as_view(), name='user-chapters'),
]
