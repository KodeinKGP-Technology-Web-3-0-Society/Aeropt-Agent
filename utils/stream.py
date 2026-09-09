import contextvars
from typing import Callable, Optional

# context variable holding a callable that accepts a string token
stream_callback_var: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar(
    "stream_callback_var", default=None
)
