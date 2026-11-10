"""Mock tests: Smart, Custom, and official JEE PYQ papers.

Question selection lives in engine.py. The HTTP routes are mounted from api_server.py.
Attempt rows in mock_tests stay; a new paper stores its blueprint and section
instructions inside the existing config jsonb.
"""

__all__ = ["settings"]
