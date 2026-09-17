"""A scripted RISCO panel on 127.0.0.1 for end-to-end tests.

It speaks the real wire format - the same framing, CRC and panel-id
encryption as a LightSYS 2 - so RiscoLocal runs unmodified over a real TCP
socket. Only its frame reader and CRC check are written independently of
pyrisco; escaping and the encryption keystream reuse pyrisco's RiscoCrypt, so
a bug shared there would not show up in these tests (the captured-frame tests
in test_risco_socket_resilience.py cover those).

Tests change its behaviour mid-session to reproduce failures seen in the
field: a link that goes silent, a panel that closes or resets the connection,
error replies with and without a command id, and replies that arrive out of
order.
"""

import asyncio
import random
import socket
import struct
import sys
import time

from pyrisco.local.risco_crypt import DLE, END, RiscoCrypt


def _crc16_modbus(data):
  """Written here rather than taken from pyrisco, like the frame reader below."""
  crc = 0xFFFF
  for byte in data:
    crc ^= byte
    for _ in range(8):
      crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
  return f'{crc:04X}'


def _crc_matches(decrypted):
  body, _, crc = decrypted.rpartition(b'\x17')
  return _crc16_modbus(body + b'\x17') == crc.decode('ascii', errors='replace')


async def _read_frame(reader):
  """Read one frame, scanning escapes byte by byte from the start.

  Deliberately a different algorithm from pyrisco's reader, so the panel
  and the client under test cannot share a framing bug.
  """
  frame = bytearray()
  escaped = False
  while True:
    byte = await reader.readexactly(1)
    frame += byte
    if escaped:
      escaped = False
    elif byte == DLE:
      escaped = True
    elif byte == END:
      return bytes(frame)

# Rule actions, set per command in ScriptedPanel.rules.
SILENT = 'silent'          # never answer
EXECUTE_NO_REPLY = 'execute-no-reply'  # act on the command, send neither reply nor push
REFUSE = 'refuse'          # answer refusal_code (N05) with the command id
REFUSE_NO_ID = 'refuse-no-id'  # answer refusal_code (N05) without a command id
CLOSE = 'close'            # close the connection instead of answering
RESET = 'reset'            # abort the connection (RST) instead of answering
LATE_REFUSE = 'late-refuse'  # answer N05 with the command id, after late_delay


class Session:
  def __init__(self, writer):
    self.writer = writer
    self.opened_at = time.monotonic()
    self.closed_at = None
    self.received = []
    # The id each command was last sent with.
    self.ids = {}
    self.encrypted = False
    self.silent = False
    # Set to encrypt replies with the wrong keystream (a desynced session).
    self.reply_crypt = None
    # Frames from the client whose CRC did not match.
    self.bad_crc = 0

  @property
  def open(self):
    return self.closed_at is None


class ScriptedPanel:

  def __init__(self, zones=(1, 2, 3), partitions=(1,), panel_id=0x15, code='1234'):
    self.zones = set(zones)
    self.partitions = set(partitions)
    self.panel_id = panel_id
    self.code = code
    # command -> action, or a list of actions consumed one per occurrence
    # (None in the list means "answer normally").
    self.rules = {}
    self.sessions = []
    # Out-of-order mode: each reply is delayed by a random amount, and some
    # are preceded by an unrelated id-less N05.
    self.scramble = None
    # Zone labels other than "Zone n", and the encoding replies are sent in.
    self.labels = {}
    self.encoding = 'utf-8'
    # Zone statuses other than '----'; partition flags ('A' armed, 'H' home).
    self.statuses = {}
    self.partition_flags = {}
    # command -> a push to send just before answering it (once).
    self.push_before = {}
    # command -> a reply to send under the command's own id just before its
    # real reply (once): a stale duplicate of an earlier reply.
    self.duplicate_before = {}
    # Encrypt from the first reply, as a panel still in the previous
    # session's state does; and a delay before every reply.
    self.encrypt_from_start = False
    # The error REFUSE answers with.
    self.refusal_code = 'N05'
    # An encrypted push sent in the same write as the RID reply.
    self.push_after_rid = None
    self.reply_delay = 0
    self.panel_type = 'RP432'
    # How long LATE_REFUSE holds its answer back.
    self.late_delay = 0.3
    self.port = None
    self._server = None
    self._push_id = 50

  async def start(self):
    self._server = await asyncio.start_server(self._handle, '127.0.0.1', 0)
    self.port = self._server.sockets[0].getsockname()[1]
    return self

  async def stop(self):
    if self._server is not None:
      self._server.close()
    for session in self.sessions:
      if session.open:
        session.writer.close()
    if self._server is not None:
      try:
        await asyncio.wait_for(self._server.wait_closed(), 1)
      except asyncio.TimeoutError:
        pass

  @property
  def open_sessions(self):
    return [s for s in self.sessions if s.open]

  def go_silent(self):
    """The link dies without a close: the open sessions stop answering."""
    for session in self.open_sessions:
      session.silent = True

  def close_sessions(self):
    for session in self.open_sessions:
      session.writer.close()

  def reset_sessions(self):
    """Send a TCP RST: abort with SO_LINGER 0, so the client sees a reset."""
    linger = struct.pack('HH' if sys.platform == 'win32' else 'ii', 1, 0)
    for session in self.open_sessions:
      sock = session.writer.get_extra_info('socket')
      if sock is not None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
      session.writer.transport.abort()

  def desync_sessions(self):
    """Encrypt every later reply with another panel's keystream."""
    for session in self.open_sessions:
      session.reply_crypt = RiscoCrypt()
      session.reply_crypt.set_panel_id(self.panel_id ^ 0x5A5A)

  def push(self, body):
    """Send an unsolicited status update to every open session."""
    for session in self.open_sessions:
      if not session.silent:
        self._push_to(session, body)

  def _push_to(self, session, body):
    self._push_id = self._push_id + 1 if self._push_id < 99 else 50
    self._write(session, self._push_id, body)

  def _pushes_after(self, command):
    """Update and push the partition status after arming or the zone status after bypassing."""
    for prefix, flags in (('ARM=', 'A'), ('STAY=', 'H'), ('DISARM=', '')):
      if command.startswith(prefix) and command[len(prefix):].isdigit():
        partition = int(command[len(prefix):])
        self.partition_flags[partition] = flags
        return [f'PSTT{partition}={self._partition_status(partition)}']
    if command.startswith('ZBYPAS=') and command[7:].isdigit():
      zone = int(command[7:])
      status = self.statuses.get(zone, '----')
      # ZBYPAS toggles; 'Y' is the bypass flag.
      status = status.replace('Y', '-') if 'Y' in status else status[:3] + 'Y' + status[4:]
      self.statuses[zone] = status
      return [f'ZSTT{zone}={status}']
    return []

  def _partition_status(self, partition):
    if partition not in self.partitions:
      return '-----'
    return f'E{self.partition_flags.get(partition, "")}----'

  async def _handle(self, reader, writer):
    session = Session(writer)
    session.encrypted = self.encrypt_from_start
    self.sessions.append(session)
    crypt = RiscoCrypt()
    crypt.set_panel_id(self.panel_id)
    session.crypt = crypt
    try:
      while True:
        frame = await _read_frame(reader)
        cmd_id, command, _ = crypt.decode(frame)
        if not _crc_matches(crypt._decrypt_chars(frame)):
          # Checked with this module's own CRC, not pyrisco's.
          session.bad_crc += 1
          self._write(session, None, 'N04')  # its id cannot be trusted
          continue
        session.received.append(command)
        session.ids[command] = cmd_id
        if command == 'ACK' or session.silent:
          continue
        action = self._action_for(command)
        if action == SILENT:
          continue
        if action == EXECUTE_NO_REPLY:
          self._pushes_after(command)  # the state changes; nothing says so
          continue
        if action == CLOSE:
          writer.close()
          return
        if action == RESET:
          writer.transport.abort()
          return
        if action == LATE_REFUSE:
          asyncio.get_running_loop().call_later(
              self.late_delay, self._write, session, cmd_id, 'N05', session.encrypted)
          continue
        if action == REFUSE:
          reply, reply_id = self.refusal_code, cmd_id
        elif action == REFUSE_NO_ID:
          reply, reply_id = self.refusal_code, None
        else:
          reply, reply_id = self._answer(command), cmd_id
        before = self.push_before.pop(command, None)
        if before is not None:
          self._push_to(session, before)
        duplicate = self.duplicate_before.pop(command, None)
        if duplicate is not None:
          self._write(session, reply_id, duplicate)
        if self.reply_delay:
          await asyncio.sleep(self.reply_delay)

        if self.scramble is not None:
          delay = self.scramble.uniform(0, 0.03)
          if self.scramble.random() < 0.15:
            self._write(session, None, 'N05')
          asyncio.get_running_loop().call_later(
              delay, self._write, session, reply_id, reply, session.encrypted)
        else:
          self._write(session, reply_id, reply)
        if action is None:  # a refused command changes nothing
          for body in self._pushes_after(command):
            self._push_to(session, body)
        if command == 'RID':
          # Everything after the panel id goes out encrypted.
          session.encrypted = True
          if self.push_after_rid is not None:
            self._push_to(session, self.push_after_rid)
        if command == 'DCN':
          writer.close()
          return
    except (asyncio.IncompleteReadError, ConnectionError):
      pass
    finally:
      session.closed_at = time.monotonic()
      writer.close()

  def _action_for(self, command):
    rule = self.rules.get(command)
    if isinstance(rule, list):
      return rule.pop(0) if rule else None
    return rule

  def _answer(self, command):
    if command == 'RID':
      return f'RID={self.panel_id:04X}'
    if command in ('LCL', 'DCN'):
      return 'ACK'
    if command.startswith('RMT='):
      return 'ACK' if command[4:] == self.code else 'N06'
    if command == 'CLOCK':
      return 'CLOCK=16/09/2026 12:00'
    if command.startswith('CLOCK='):
      return 'ACK'
    if command.split('=')[0] in ('ARM', 'STAY', 'DISARM', 'ZBYPAS'):
      return 'ACK'

    key = command.rstrip('?')
    fixed = {
        'PNLCNF': self.panel_type,
        'FSVER': '6.07',
        'PNLSERD': '12345678901',
        'SYSLBL': 'Home',
        'SSTT': '----',
    }
    if key in fixed:
      return f'{key}={fixed[key]}'

    for prefix, handler in (
        ('ZTYPE*', lambda n: '1' if n in self.zones else '0'),
        ('ZLNKTYP', lambda n: 'E' if n in self.zones else 'N'),
        ('ZSTT*', lambda n: self.statuses.get(n, '----')),
        ('ZLBL*', lambda n: self.labels.get(n, f'Zone {n}')),
        ('ZPART&*', lambda n: '1'),
        ('ZAREA&*', lambda n: '0'),
        ('PSTT', self._partition_status),
        ('PLBL', lambda n: f'Partition {n}'),
    ):
      if key.startswith(prefix) and key[len(prefix):].isdigit():
        # A real LightSYS 2 drops the '*' from the key in its reply:
        # ZSTT*1? is answered ZSTT1=...
        return f"{key.replace('*', '')}={handler(int(key[len(prefix):]))}"

    return 'N05'

  def _write(self, session, cmd_id, body, encrypted=None):
    if not session.open or session.writer.is_closing():
      return
    # A delayed reply keeps the encryption it was answered under: the RID
    # reply must go out in the clear even if it lands after encryption began.
    if encrypted is None:
      encrypted = session.encrypted
    crypt = session.reply_crypt or session.crypt
    data = ((f'{cmd_id:02d}' if cmd_id is not None else '') + body + '\x17').encode(self.encoding)
    data += _crc16_modbus(data).encode('ascii')
    chars = crypt._encrypt_chars(bytearray(data), encrypted)
    frame = bytes([2]) + (bytes([17]) if encrypted else b'') + bytes(chars) + bytes([3])
    session.writer.write(frame)


class Supervisor:
  """Does what Home Assistant does with a RiscoLocal.

  Connects, retrying on CannotConnectError; registers an error handler after
  connecting; and on a lost connection reloads - disconnects the old
  instance and connects a new one.
  """

  def __init__(self, local_class, port, retry_delay=0.1, lost=None, **kwargs):
    self._local_class = local_class
    self._port = port
    self._retry_delay = retry_delay
    # The error types it reloads on; by default, what this library reports.
    self._lost = lost
    self._kwargs = kwargs
    self.panel = None
    self.errors = []
    self.reloads = 0
    self.ready = asyncio.Event()
    self._tasks = set()

  async def start(self):
    await self._setup()

  async def stop(self):
    for task in list(self._tasks):
      task.cancel()
    if self.panel is not None:
      await self.panel.disconnect()

  async def _setup(self):
    from pyrisco.common import CannotConnectError
    while True:
      panel = self._local_class('127.0.0.1', self._port, '1234', **self._kwargs)
      try:
        await panel.connect()
        break
      except CannotConnectError:
        await asyncio.sleep(self._retry_delay)
    panel.add_error_handler(self._on_error)
    self.panel = panel
    self.ready.set()

  async def _on_error(self, error):
    from pyrisco import common
    lost = self._lost or getattr(
        common, 'ConnectionLostError', (OSError, EOFError, asyncio.LimitOverrunError))
    self.errors.append(error)
    if isinstance(error, lost):
      task = asyncio.create_task(self._reload())
      self._tasks.add(task)
      task.add_done_callback(self._tasks.discard)

  async def _reload(self):
    self.reloads += 1
    self.ready.clear()
    old, self.panel = self.panel, None
    if old is not None:
      await old.disconnect()
    await self._setup()
