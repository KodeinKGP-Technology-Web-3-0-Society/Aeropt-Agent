"""
analytics/urls.py
=================
Mounted at: /api/v1/analytics/

All routes below are relative to /api/v1/analytics/.
"""

from django.urls import path
from analytics.views import DashboardView

urlpatterns = [
    # GET /api/v1/analytics/dashboard/
    path('dashboard/', DashboardView.as_view(), name='analytics-dashboard'),
]
