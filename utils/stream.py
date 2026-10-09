import contextvars
import re
import time
from typing import Callable, Optional

# context variable holding a callable that accepts a string token
stream_callback_var: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar(
    "stream_callback_var", default=None
)

# The reply is streamed after the Answer Checker approves it, so the text is
# already complete. Pace it like typing, but never let pacing itself become a
# wait: a long answer gets bigger chunks, not a longer animation.
_TOKEN = re.compile(r"\S+\s*|\s+")
_MAX_STREAM_S = 1.5
_MAX_CHUNKS = 150


def emit_text(text: str, cb: Optional[Callable[[str], None]] = None) -> None:
    """Send finished text to the stream callback as word-sized tokens.

    No-op when nothing is listening (a non-streaming caller or a test)."""
    cb = cb or stream_callback_var.get()
    if not cb or not text:
        return
    words = _TOKEN.findall(text)
    per_chunk = max(1, -(-len(words) // _MAX_CHUNKS))
    chunks = ["".join(words[i : i + per_chunk]) for i in range(0, len(words), per_chunk)]
    delay = min(0.01, _MAX_STREAM_S / max(1, len(chunks)))
    for chunk in chunks:
        cb(chunk)
        time.sleep(delay)
