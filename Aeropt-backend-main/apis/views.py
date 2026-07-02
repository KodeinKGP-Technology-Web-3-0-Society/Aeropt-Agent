"""
VIEWS — apis app
================
Only the health-check view lives here. All domain views are in their respective apps.

Planned views:

HealthCheckView  (APIView)
  - get()  GET /api/v1/health/
    Returns {"status": "ok", "version": "1.0.0"}
    No authentication required.

Shared utilities (NOT views — see apis/utils.py, apis/pagination.py, apis/permissions.py):
  - success_response(data, status=200)
  - error_response(errors, status=400)
  - custom_exception_handler(exc, ctx)   → registered via REST_FRAMEWORK settings
  - StandardResultsPagination            → page_size=20
  - IsOwner                              → object-level ownership check
"""

from django.shortcuts import render

# Views will be implemented here.
