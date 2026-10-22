import contextvars
import re
import time
from typing import Callable, Optional

# context variable holding a callable that accepts a string token
stream_callback_var: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar(
    "stream_callback_var", default=None
)

# Swap the whole reply: the checker revised a draft that was already streamed.
stream_replace_var: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar(
    "stream_replace_var", default=None
)


def emit_replace(text: str) -> None:
    """Tell the stream that the reply it has shown so far is superseded by `text`."""
    cb = stream_replace_var.get()
    if cb and text:
        cb(text)


# The reply is streamed after the Answer Checker approves it, so the text is
# already complete. Pace it like typing, but never let pacing itself become a
# wait: a long answer gets bigger chunks, not a longer animation.
_TOKEN = re.compile(r"\S+\s*|\s+")
_MIN_STREAM_S = 1.2
_MAX_STREAM_S = 4.0


def emit_text(text: str, cb: Optional[Callable[[str], None]] = None) -> None:
    """Send finished text to the stream callback as word-sized tokens.

    No-op when nothing is listening (a non-streaming caller or a test)."""
    cb = cb or stream_callback_var.get()
    if not cb or not text:
        return
    words = _TOKEN.findall(text)
    if not words:
        return
    # Pace progressively: ~25ms per word, bounded between 1.2s and 4.0s
    target_time_s = max(_MIN_STREAM_S, min(_MAX_STREAM_S, len(words) * 0.025))
    target_chunks = max(20, min(len(words), int(target_time_s / 0.025)))
    per_chunk = max(1, -(-len(words) // target_chunks))
    chunks = ["".join(words[i : i + per_chunk]) for i in range(0, len(words), per_chunk)]
    delay = max(0.012, target_time_s / max(1, len(chunks)))
    for chunk in chunks:
        cb(chunk)
        time.sleep(delay)
