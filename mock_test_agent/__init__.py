"""Dedicated mock-test package.

Nothing in the running tutor imports this package. The HTTP routes live in
``router.py`` and stay unmounted until someone explicitly includes that router
on the FastAPI app.
"""

__all__ = ["settings"]
