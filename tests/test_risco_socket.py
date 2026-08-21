import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from pyrisco.common import OperationError
from pyrisco.local.risco_socket import MAX_CMD_ID, RiscoSocket


class ListenLoopShutdownTest(unittest.IsolatedAsyncioTestCase):
    def _make_socket(self):
        sock = RiscoSocket('host', 1, '1234')
        sock._queue = asyncio.Queue()
        sock._futures = [None] * MAX_CMD_ID
        sock._reader = MagicMock()
        return sock

    async def test_incomplete_read_breaks_loop_and_cancels_futures(self):
        sock = self._make_socket()
        pending = asyncio.get_running_loop().create_future()
        sock._futures[0] = pending
        sock._reader.readuntil = AsyncMock(
            side_effect=asyncio.IncompleteReadError(b'', None)
        )

        await asyncio.wait_for(sock._listen(), timeout=1)

        self.assertTrue(pending.done())
        with self.assertRaises(OperationError):
            pending.result()
        self.assertIsInstance(sock._queue.get_nowait(), asyncio.IncompleteReadError)

    async def test_connection_reset_breaks_loop_and_cancels_futures(self):
        sock = self._make_socket()
        pending = asyncio.get_running_loop().create_future()
        sock._futures[0] = pending
        sock._reader.readuntil = AsyncMock(side_effect=ConnectionResetError())

        await asyncio.wait_for(sock._listen(), timeout=1)

        self.assertTrue(pending.done())
        with self.assertRaises(OperationError):
            pending.result()
        self.assertIsInstance(sock._queue.get_nowait(), ConnectionResetError)

    async def test_broken_pipe_breaks_loop_and_cancels_futures(self):
        # BrokenPipeError is a ConnectionError but not a ConnectionResetError,
        # so this proves the wider ConnectionError base is what's caught.
        sock = self._make_socket()
        pending = asyncio.get_running_loop().create_future()
        sock._futures[0] = pending
        sock._reader.readuntil = AsyncMock(side_effect=BrokenPipeError())

        await asyncio.wait_for(sock._listen(), timeout=1)

        self.assertTrue(pending.done())
        with self.assertRaises(OperationError):
            pending.result()
        self.assertIsInstance(sock._queue.get_nowait(), BrokenPipeError)

    async def test_recoverable_error_is_queued_without_breaking(self):
        # A non-connection error must be surfaced on the queue but must NOT
        # tear down the listener; only a real connection loss ends the loop.
        sock = self._make_socket()
        sock._reader.readuntil = AsyncMock(
            side_effect=[RuntimeError('boom'), ConnectionResetError()]
        )

        await asyncio.wait_for(sock._listen(), timeout=1)

        first = sock._queue.get_nowait()
        second = sock._queue.get_nowait()
        self.assertIsInstance(first, RuntimeError)
        self.assertIsInstance(second, ConnectionResetError)


if __name__ == '__main__':
    unittest.main()
