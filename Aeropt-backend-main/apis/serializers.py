"""
apis/serializers.py
===================
Shared serializer mixins and base classes used across all apps.

PLANNED CONTENT
---------------

StandardResponseSerializer  (base mixin)
  - Wraps any serializer output in:
    { "success": true, "data": <serializer output>, "errors": null }
  - On error:
    { "success": false, "data": null, "errors": [{"field": "...", "message": "..."}] }

ErrorDetailSerializer
  - Fields: field (str or "non_field_errors"), message

HealthCheckSerializer
  - Fields: status, version
  - Used by GET /api/v1/health/

Note: Each app has its own serializers.py for domain-specific serializers.
      This file is only for shared base classes.
"""

# Serializers will be implemented here.
