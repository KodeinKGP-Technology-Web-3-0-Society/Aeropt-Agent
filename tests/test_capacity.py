"""The turn gate caps concurrent tutor turns and sheds the overflow."""

from __future__ import annotations

import asyncio
import unittest

from services.capacity import TurnGate


class TestTurnGate(unittest.TestCase):
    def test_admits_up_to_limit_then_queues_then_rejects(self):
        async def scenario():
            gate = TurnGate(max_active=1, max_waiting=1, wait_s=0.2)
            self.assertTrue(await gate.acquire())          # takes the slot
            waiter = asyncio.create_task(gate.acquire())   # fills the queue
            await asyncio.sleep(0.01)
            self.assertFalse(await gate.acquire())         # queue full: rejected at once
            gate.release()
            self.assertTrue(await waiter)                  # waiter gets the freed slot
            gate.release()
            self.assertEqual(gate.stats()["rejected"], 1)
            self.assertEqual(gate.stats()["active"], 0)

        asyncio.run(scenario())

    def test_queue_wait_times_out(self):
        async def scenario():
            gate = TurnGate(max_active=1, max_waiting=2, wait_s=0.05)
            self.assertTrue(await gate.acquire())
            self.assertFalse(await gate.acquire())
            self.assertEqual(gate.stats()["waiting"], 0)

        asyncio.run(scenario())

    def test_run_keeps_contextvars(self):
        import contextvars

        var = contextvars.ContextVar("v", default="unset")

        async def scenario():
            gate = TurnGate(max_active=1)
            var.set("kept")
            return await gate.run(lambda: var.get())

        self.assertEqual(asyncio.run(scenario()), "kept")


if __name__ == "__main__":
    unittest.main()
