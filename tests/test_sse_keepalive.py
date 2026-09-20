"""
A tutor turn is silent until the answer checker passes, and on a slower model
provider it can run for over a minute. The browser client aborts a stream after 90 s
without bytes, so the server must keep traffic flowing. An SSE comment line does that
and is ignored by every client.
"""

from __future__ import annotations

import asyncio
import json
import unittest

import api_server


async def _collect(queue: "asyncio.Queue", keepalive_s: float, feed) -> list:
    frames: list = []

    async def consume():
        async for frame in api_server._sse_frames(queue, keepalive_s=keepalive_s):
            frames.append(frame)

    feeder = asyncio.create_task(feed(queue))
    await asyncio.wait_for(consume(), timeout=5)
    await feeder
    return frames


class TestSseFrames(unittest.TestCase):
    def test_silence_emits_keepalive_comments_then_the_real_frames(self):
        async def feed(q):
            await asyncio.sleep(0.16)  # several keepalive intervals of silence
            q.put_nowait({"type": "token", "data": "Hello"})
            q.put_nowait({"type": "result", "data": '{"ok": true}'})

        frames = asyncio.run(_collect(asyncio.Queue(), 0.04, feed))

        keepalives = [f for f in frames if f.startswith(":")]
        self.assertGreaterEqual(len(keepalives), 2)
        self.assertTrue(all(f == ": keepalive\n\n" for f in keepalives))
        # Real frames arrive intact and in order, and the stream ends after `result`.
        self.assertEqual(frames[-2], f"event: token\ndata: {json.dumps('Hello')}\n\n")
        self.assertEqual(frames[-1], 'event: result\ndata: {"ok": true}\n\n')

    def test_a_fast_turn_sends_no_keepalive_at_all(self):
        async def feed(q):
            q.put_nowait({"type": "token", "data": "hi"})
            q.put_nowait({"type": "result", "data": "{}"})

        frames = asyncio.run(_collect(asyncio.Queue(), 5.0, feed))
        self.assertEqual(len(frames), 2)
        self.assertFalse(any(f.startswith(":") for f in frames))

    def test_a_keepalive_frame_is_invisible_to_the_frontend_parser(self):
        # Mirror Aeropt-frontend/src/lib/api/agent.ts: an event block only counts when
        # it carries `data: `. A comment block carries none, so nothing is dispatched.
        block = ": keepalive"
        event_data = ""
        for line in block.split("\n"):
            if line.startswith("data: "):
                event_data = line[6:].strip()
        self.assertEqual(event_data, "")


if __name__ == "__main__":
    unittest.main()
