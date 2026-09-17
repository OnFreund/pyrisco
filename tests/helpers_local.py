"""Shared helpers for the local (socket) tests.

These helpers deliberately avoid `unittest.mock.AsyncMock` for the reader:
an AsyncMock whose side_effect is an exception raises *without ever yielding
to the event loop*, so a buggy `while True` listener spins in a tight loop
that `asyncio.wait_for` can never interrupt - the whole test run hangs
instead of failing. Every fake below yields at least once per call, so a
test that exposes a spin bug fails on its timeout instead of wedging the
runner.
"""

import asyncio
import unittest.mock

from pyrisco.common import OperationError
from pyrisco.local import risco_socket

# pyrisco.common.CommunicationError does not exist before this change; fall
# back so the tests can still be run against the old code for comparison.
from pyrisco import common as _common
CommunicationError = getattr(_common, 'CommunicationError', OperationError)
ConnectionLostError = getattr(_common, 'ConnectionLostError', CommunicationError)

# Every test that drives a `while True` loop must be wrapped in this.
LOOP_TIMEOUT = 1


def scripted_reader(sock, frames):
  """Make `sock._read_command()` return `frames` in order.

  An element that is an exception instance is raised instead of returned.
  Once the script is exhausted the reader raises ConnectionResetError, which
  terminates the listener.
  """
  items = iter(frames)

  async def _read():
    await asyncio.sleep(0)
    try:
      item = next(items)
    except StopIteration:
      raise ConnectionResetError from None
    if isinstance(item, BaseException):
      raise item
    return item

  sock._read_command = _read


class FeedReader:
  """A reader the test feeds one decoded frame at a time.

  Unlike scripted_reader it never runs out: the listener waits on it until
  the test pushes the next frame, so a test can interleave replies with
  commands that are still being sent.
  """

  def __init__(self, sock):
    self._frames = asyncio.Queue()
    sock._read_command = self._read

  def push(self, cmd_id, command, crc=True):
    self._frames.put_nowait((cmd_id, command, crc))

  def fail(self, error):
    self._frames.put_nowait(error)

  async def _read(self):
    item = await self._frames.get()
    if isinstance(item, BaseException):
      raise item
    return item


class FakeTransport:
  def __init__(self):
    self.closing = False
    self.closed = False
    self.aborted = False
    self.buffered = 0

  def is_closing(self):
    return self.closing

  def get_write_buffer_size(self):
    return self.buffered

  def close(self):
    self.closing = True
    self.closed = True

  def abort(self):
    self.closing = True
    self.aborted = True


class FakeWriter:
  """Records what RiscoSocket writes, as (cmd_id, command) pairs."""

  def __init__(self):
    self.transport = FakeTransport()
    self.written = []
    self.wait_closed_calls = 0
    self.wait_closed_hangs = False

  def write(self, data):
    self.written.append(data)

  def close(self):
    self.transport.close()

  async def wait_closed(self):
    self.wait_closed_calls += 1
    if self.wait_closed_hangs:
      await asyncio.sleep(3600)


def connected_socket(concurrency=4, **kwargs):
  """A RiscoSocket that looks connected, with no network underneath.

  Commands written are captured on `sock.sent` as (cmd_id, command).
  """
  sock = risco_socket.RiscoSocket('host', 1, '1234', concurrency=concurrency, **kwargs)
  sock._queue = asyncio.Queue()
  sock._futures = [None] * risco_socket.MAX_CMD_ID
  sock._cmd_id = 0
  sock._semaphore = asyncio.Semaphore(concurrency)
  sock._writer = FakeWriter()
  sock.sent = []

  def _write(cmd_id, command, force_encryption=False):
    sock.sent.append((cmd_id, command))

  sock._write_command = _write
  return sock


def drain(queue):
  """Return everything currently on an asyncio.Queue, without blocking."""
  items = []
  while True:
    try:
      items.append(queue.get_nowait())
    except asyncio.QueueEmpty:
      return items


async def settle(rounds=40):
  """Let background tasks and handler callbacks run."""
  for _ in range(rounds):
    await asyncio.sleep(0)


async def serve(test, handler):
  """Run `handler` as a TCP server on 127.0.0.1 for one test; return the port.

  The server side of every connection is closed when the handler returns.
  Without that, Server.wait_closed() (3.12+) waits forever for connections
  the handler abandoned.
  """
  async def _handle(reader, writer):
    try:
      await handler(reader, writer)
    finally:
      writer.close()

  server = await asyncio.start_server(_handle, '127.0.0.1', 0)

  async def _stop():
    server.close()
    try:
      await asyncio.wait_for(server.wait_closed(), LOOP_TIMEOUT)
    except asyncio.TimeoutError:
      pass

  test.addAsyncCleanup(_stop)
  return server.sockets[0].getsockname()[1]


def reset_reconnect_history():
  """Reconnect pacing is process-wide; tests must not leak it into each other."""
  reset = getattr(risco_socket, 'reset_reconnect_history', None)
  if reset is not None:
    reset()
  elif getattr(risco_socket, '_panel_history', None) is not None:
    risco_socket._panel_history.clear()  # versions before the public reset


class FakeSocket:
  """Stand-in for RiscoSocket, for exercising RiscoLocal in isolation.

  `errors`: commands the panel always rejects (an N05 with a command id).
  `failures`: {command: [outcome, ...]} consumed one per call, where an
  outcome is an exception to raise or None to answer normally.
  """

  def __init__(self, responses=None, errors=(), failures=None, default=None):
    self.responses = dict(responses or {})
    self.errors = set(errors)
    self.failures = {k: list(v) for k, v in (failures or {}).items()}
    self.default = default
    self.concurrency = 4
    self.close_reason = None
    # Receive order send_status_query reports per command (default 0).
    self.reply_seqs = {}
    self.queue = asyncio.Queue()
    self.connected = False
    self.connect_calls = 0
    self.disconnect_calls = 0
    self.abort_calls = 0
    self.disconnect_error = None
    self.block_on = None
    self.blocked = asyncio.Event()
    self.sent = []

  async def connect(self):
    self.connect_calls += 1
    self.connected = True

  async def disconnect(self):
    self.disconnect_calls += 1
    if self.disconnect_error is not None:
      raise self.disconnect_error
    self.connected = False

  def abort(self):
    self.abort_calls += 1
    self.connected = False

  async def send_command(self, command, force_encryption=False, timeout=None):
    self.sent.append(command)
    if command == self.block_on:
      self.blocked.set()
      await asyncio.sleep(3600)
    pending = self.failures.get(command)
    if pending:
      outcome = pending.pop(0)
      if outcome is not None:
        raise outcome
    if command in self.errors:
      raise OperationError('cmd_id: 1, Risco error: N05')
    if command in self.responses:
      return self.responses[command]
    if self.default is not None:
      return self.default(command)
    raise AssertionError(f'FakeSocket got an unscripted command: {command}')

  async def send_result_command(self, command, timeout=None):
    return await self.send_command(command, timeout=timeout)

  async def send_status_query(self, command):
    return await self.send_command(command), self.reply_seqs.get(command, 0)

  async def send_ack_command(self, command, timeout=None):
    await self.send_command(command, timeout=timeout)
    return True


# RP432M, firmware 6.07.
# panel_capabilities() normalises on ":" and looks up "RP432", giving
# MAX_ZONES=50 and MAX_PARTS=4 at firmware >= 3.
PANEL_TYPE = 'RP432'
PANEL_FIRMWARE = '6.07'
MAX_ZONES = 50
MAX_PARTS = 4


def legacy_panel_responses(zones=(1,), partitions=(1,)):
  """Replies from an Agility (RW132): no FSVER?, ZLNKTYP or ZAREA queries.

  panel_capabilities('RW132', '') gives 36 zones and 3 partitions.
  """
  responses = {
      'PNLCNF': 'RW132',
      'PNLSERD': '7654321',
      'SYSLBL?': 'Cottage',
      'SSTT?': '----',
  }
  for i in range(1, 4):
    responses[f'PSTT{i}?'] = 'E----' if i in partitions else '----'
    responses[f'PLBL{i}?'] = f'Partition {i}'
  for i in range(1, 37):
    if i in zones:
      responses[f'ZTYPE*{i}?'] = '1'
      responses[f'ZSTT*{i}?'] = '----'
      responses[f'ZLBL*{i}?'] = f'Zone {i}'
      responses[f'ZPART&*{i}?'] = '1'
    else:
      responses[f'ZTYPE*{i}?'] = '0'
  return responses


def panel_responses(zones=(1,), partitions=(1,)):
  """Build a full reply table for RiscoLocal.connect() on an RP432/6.07.

  Zones and partitions not listed answer "does not exist" the way a real
  panel does (zone type 0 / a partition status with no 'E'), rather than
  erroring - that distinction is the whole point of several tests.
  """
  responses = {
      'PNLCNF': PANEL_TYPE,
      'FSVER?': PANEL_FIRMWARE,
      'PNLSERD': '1234567',
      'SYSLBL?': 'Home',
      'SSTT?': '----',
  }
  for i in range(1, MAX_PARTS + 1):
    if i in partitions:
      responses[f'PSTT{i}?'] = 'E----'
      responses[f'PLBL{i}?'] = f'Partition {i}'
    else:
      responses[f'PSTT{i}?'] = '----'
  for i in range(1, MAX_ZONES + 1):
    if i in zones:
      responses[f'ZTYPE*{i}?'] = '1'
      responses[f'ZLNKTYP{i}?'] = 'E'
      responses[f'ZSTT*{i}?'] = '----'
      responses[f'ZLBL*{i}?'] = f'Zone {i}'
      responses[f'ZPART&*{i}?'] = '1'
      responses[f'ZAREA&*{i}?'] = '0'
    else:
      responses[f'ZTYPE*{i}?'] = '0'
  return responses


def patch_timing(**overrides):
  """Shrink the socket's timing constants so real-time tests run fast."""
  values = dict(
      COMMAND_TIMEOUT=0.2,
      DISCONNECT_TIMEOUT=0.2,
      KEEP_ALIVE_INTERVAL=0.05,
      RECONNECT_DELAY=0.2,
      STABLE_SESSION=2.0,
      MAX_RECONNECT_DELAY=1.6,
  )
  values.update(overrides)
  return unittest.mock.patch.multiple(risco_socket, create=True, **values)
