"""End to end: RiscoLocal against a scripted panel over real TCP.

Each test reproduces a failure seen in the field or in live fault injection,
with the socket's timing constants shrunk so the whole file runs in seconds.
The one invariant checked throughout is the panel's: it must never see two
sessions open at once, and never a new session sooner than RECONNECT_DELAY
after the previous one closed.
"""

import asyncio
import random
import sys
import time
import unittest

from helpers_local import (
    CommunicationError,
    ConnectionLostError,
    patch_timing,
    reset_reconnect_history,
)
from pyrisco.common import CannotConnectError, OperationError, UnauthorizedError
from pyrisco.local.risco_local import RiscoLocal
from scripted_panel import (
    CLOSE,
    LATE_REFUSE,
    REFUSE,
    EXECUTE_NO_REPLY,
    REFUSE_NO_ID,
    SILENT,
    ScriptedPanel,
    Supervisor,
)

# Matches helpers_local.patch_timing().
RECONNECT_DELAY = 0.2
MAX_RECONNECT_DELAY = 1.6
# Scheduling slack allowed when comparing wall-clock gaps.
SLACK = 0.05
WAIT = 5


def _losses(supervisor):
  """What ended each lost connection the supervisor was told of."""
  return [e.__cause__ if isinstance(e, ConnectionLostError) else e for e in supervisor.errors]


async def _until(predicate, timeout=WAIT, what='condition'):
  deadline = time.monotonic() + timeout
  while not predicate():
    if time.monotonic() > deadline:
      raise AssertionError(f'timed out waiting for {what}')
    await asyncio.sleep(0.01)


async def _wait(predicate, timeout=10):
  deadline = time.monotonic() + timeout
  while not predicate():
    if time.monotonic() > deadline:
      raise AssertionError('timed out')
    await asyncio.sleep(0.01)


def _discovery_tasks():
  return [t for t in asyncio.all_tasks() if not t.done()
          and t.get_coro().__qualname__.startswith('RiscoLocal._get_objects')]


class ScriptedPanelTestCase(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    reset_reconnect_history()
    self.addCleanup(reset_reconnect_history)
    timing = patch_timing()
    timing.start()
    self.addCleanup(timing.stop)
    self.panel = await ScriptedPanel().start()
    self.addAsyncCleanup(self.panel.stop)

  def supervisor(self, **kwargs):
    supervisor = Supervisor(RiscoLocal, self.panel.port, **kwargs)
    self.addAsyncCleanup(supervisor.stop)
    return supervisor

  def assert_one_session_at_a_time(self):
    sessions = self.panel.sessions
    for before, after in zip(sessions, sessions[1:]):
      self.assertIsNotNone(before.closed_at, 'Close each earlier session before starting the next one.')
      self.assertLessEqual(
          before.closed_at, after.opened_at + SLACK,
          'Keep at most one panel session open at a time.')

  def reconnect_gaps(self):
    sessions = self.panel.sessions
    return [after.opened_at - before.closed_at
            for before, after in zip(sessions, sessions[1:])]


class ConnectTest(ScriptedPanelTestCase):

  async def test_reads_the_whole_panel_over_the_encrypted_link(self):
    self.panel.zones = {1, 2, 7}
    self.panel.partitions = {1, 3}
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), [1, 2, 7])
    self.assertEqual(local.zones[7].name, 'Zone 7')
    self.assertEqual(sorted(local.partitions), [1, 3])
    self.assertEqual(local.system.name, 'Home')
    self.assertTrue(self.panel.sessions[0].encrypted)

  async def test_pushed_status_reaches_the_zone_handler(self):
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)
    seen = []

    async def _zone(zone_id, zone):
      seen.append((zone_id, zone.triggered))

    local.add_zone_handler(_zone)
    self.panel.push('ZSTT2=O---')
    await _until(lambda: seen, what='zone update')

    self.assertEqual(seen, [(2, True)])
    await _until(lambda: 'ACK' in self.panel.sessions[0].received, what='push ACK')

  async def test_refused_fsver_closes_the_session(self):
    """A refusal during connect() must not leave the session open.

    A session left open makes the panel refuse new connections.
    """
    self.panel.rules['FSVER?'] = REFUSE
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    with self.assertRaises(CannotConnectError) as caught:
      await asyncio.wait_for(local.connect(), WAIT)

    self.assertIn('N05', str(caught.exception))
    await _until(lambda: not self.panel.open_sessions, timeout=1,
                 what='the panel to see the session closed')

  async def test_a_wrong_code_is_an_authorisation_failure(self):
    """Classify an identified N06 login refusal as invalid authentication so consumers stop retrying."""
    local = RiscoLocal('127.0.0.1', self.panel.port, '9999')

    with self.assertRaises(UnauthorizedError):
      await asyncio.wait_for(local.connect(), WAIT)

    await _until(lambda: not self.panel.open_sessions, timeout=1,
                 what='the panel to see the session closed')

  async def test_a_login_refusal_other_than_n06_is_retryable(self):
    """Keep other identified login refusals retryable because they do not establish a wrong code.

    The observed panel also answered N05 to a normally supported query.
    """
    for code in ('N05', 'N16', 'N17', 'N11', 'N13', 'N01'):
      with self.subTest(code=code):
        reset_reconnect_history()
        self.panel.refusal_code = code
        self.panel.rules['RMT=1234'] = [REFUSE]
        local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

        with self.assertRaises(CannotConnectError) as caught:
          await asyncio.wait_for(local.connect(), WAIT)
        self.assertNotIsInstance(caught.exception, UnauthorizedError)

  async def test_an_unexpected_reply_to_the_login_is_retryable(self):
    """Treat an unexpected login reply as retryable rather than evidence of an incorrect code."""
    self.panel.duplicate_before['RMT=1234'] = 'ZSTT1=----'
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    with self.assertRaises(CannotConnectError) as caught:
      await asyncio.wait_for(local.connect(), WAIT)

    self.assertNotIsInstance(caught.exception, UnauthorizedError)

  async def test_a_login_refusal_without_a_command_id_times_out_as_retryable(self):
    """Leave an id-less refusal unmatched because it cannot be attributed to the login.

    Even N06 then produces a retryable timeout, so a retrying consumer sends
    the code again. The observed wrong-code reply carried the login ID; this
    test covers panels that might omit it.
    """
    self.panel.refusal_code = 'N06'
    self.panel.rules['RMT=1234'] = [REFUSE_NO_ID]
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    with self.assertRaises(CannotConnectError) as caught:
      await asyncio.wait_for(local.connect(), WAIT)

    self.assertNotIsInstance(caught.exception, UnauthorizedError)
    self.assertIn('RMT', str(caught.exception))
    self.assertNotIn('1234', str(caught.exception))
    self.assertEqual(self.panel.sessions[0].received.count('RMT=1234'), 1)

  async def test_a_control_carried_out_without_an_answer_is_not_sent_again(self):
    """Never resend a control whose acknowledgement and status push were lost.

    ZBYPAS toggles the zone, so repeating it could undo the requested change.
    Keep the last known status until another panel report arrives.
    """
    self.panel.rules['ZBYPAS=2'] = [EXECUTE_NO_REPLY]
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    with self.assertRaises(CommunicationError):
      await asyncio.wait_for(local.zones[2].bypass(True), WAIT)
    await asyncio.sleep(0.5)  # well past the command timeout

    self.assertEqual(self.panel.sessions[0].received.count('ZBYPAS=2'), 1)
    self.assertIn('Y', self.panel.statuses[2])
    self.assertFalse(local.zones[2].bypassed)
    self.assertEqual(len(self.panel.sessions), 1)

  async def test_bypassing_a_zone_is_acknowledged_and_pushed(self):
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)
    seen = []

    async def _zone(zone_id, zone):
      seen.append((zone_id, zone.bypassed))

    local.add_zone_handler(_zone)
    await asyncio.wait_for(local.zones[2].bypass(True), WAIT)
    await _until(lambda: seen, what='the pushed status')
    await asyncio.wait_for(local.zones[2].bypass(False), WAIT)
    await _until(lambda: len(seen) == 2, what='the second pushed status')

    self.assertEqual(seen, [(2, True), (2, False)])

  async def test_every_frame_the_client_sends_has_a_valid_crc(self):
    """Check outbound frames with the scripted panel's independent CRC implementation."""
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)
    await asyncio.wait_for(local.arm(1), WAIT)
    await asyncio.wait_for(local.disconnect(), WAIT)

    session = self.panel.sessions[0]
    self.assertGreater(len(session.received), 50)
    self.assertEqual(session.bad_crc, 0)

  async def test_the_panel_rejects_a_frame_with_a_bad_crc(self):
    from pyrisco.local.risco_crypt import RiscoCrypt
    reader, writer = await asyncio.open_connection('127.0.0.1', self.panel.port)
    self.addAsyncCleanup(writer.wait_closed)
    self.addCleanup(writer.close)

    writer.write(b'\x0201RID\x170000\x03')
    reply = await asyncio.wait_for(reader.readuntil(b'\x03'), WAIT)

    # Without an id: the id in a corrupted frame cannot be trusted.
    self.assertEqual(RiscoCrypt().decode(reply)[:2], [None, 'N04'])
    await _until(lambda: self.panel.sessions and self.panel.sessions[0].bad_crc == 1,
                 timeout=1, what='the count')

  async def test_a_panel_still_encrypting_fails_the_connect_at_once(self):
    """Fail promptly when the panel encrypts RID before the client knows its panel ID."""
    self.panel.encrypt_from_start = True
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    started = time.monotonic()
    with patch_timing(COMMAND_TIMEOUT=5.0):
      with self.assertRaises(CannotConnectError) as caught:
        await asyncio.wait_for(local.connect(), WAIT)

    self.assertLess(time.monotonic() - started, 1.0)
    # Preserve the shutdown reason rather than reporting only "Not connected".
    self.assertIn('still encrypting', str(caught.exception))

  async def test_a_push_right_behind_the_rid_reply_does_not_fail_the_connect(self):
    """Allow a push beside RID before connect() has stored the panel ID without rejecting setup."""
    self.panel.push_after_rid = 'CLOCK=17/09/2026 04:13'
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), sorted(self.panel.zones))

  async def test_an_unanswered_login_is_a_connection_failure(self):
    """No answer says nothing about the code, so it must stay retryable."""
    self.panel.rules['RMT=1234'] = SILENT
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(local.connect(), WAIT)

  async def test_communication_delay_is_honoured(self):
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234', communication_delay=0.3)

    started = time.monotonic()
    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertGreaterEqual(time.monotonic() - started, 0.3)

  async def test_deliberate_reconnects_wait_only_the_panel_cooldown(self):
    """Apply only the panel cooldown to deliberate reconnects such as configuration checks."""
    for _ in range(4):
      local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
      await asyncio.wait_for(local.connect(), WAIT)
      await asyncio.wait_for(local.disconnect(), WAIT)

    gaps = self.reconnect_gaps()
    self.assertEqual(len(gaps), 3)
    for gap in gaps:
      self.assertGreaterEqual(gap, RECONNECT_DELAY - SLACK, gaps)
      self.assertLess(gap, 2 * RECONNECT_DELAY - SLACK, f'escalated: {gaps}')

  async def test_cancelled_setup_closes_the_session(self):
    """HA cancels a slow setup at shutdown; the panel must not keep a session."""
    self.panel.rules['PNLSERD'] = SILENT
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    task = asyncio.create_task(local.connect())
    await _until(lambda: 'PNLSERD' in (self.panel.sessions[0].received
                                       if self.panel.sessions else []),
                 what='setup to reach PNLSERD')
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task

    await _until(lambda: not self.panel.open_sessions, timeout=1,
                 what='the panel to see the session closed')

  async def test_refused_connection_fails_fast(self):
    await self.panel.stop()
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    started = time.monotonic()
    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(local.connect(), WAIT)
    # Windows retries a refused localhost connect for about 2s by itself.
    self.assertLess(time.monotonic() - started, 4)

  async def test_unanswered_dcn_does_not_hold_up_disconnect(self):
    self.panel.rules['DCN'] = SILENT
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)

    started = time.monotonic()
    await asyncio.wait_for(local.disconnect(), WAIT)

    self.assertLess(time.monotonic() - started, 1)
    await _until(lambda: not self.panel.open_sessions, timeout=1,
                 what='the panel to see the session closed')


class DiscoveryTest(ScriptedPanelTestCase):

  async def test_an_id_less_error_during_discovery_does_not_lose_a_zone(self):
    """At the default concurrency the answer is lost; it is asked for again."""
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZTYPE*2?'] = [REFUSE_NO_ID]
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234', concurrency=4)

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), [1, 2, 3])

  async def test_a_refused_zone_query_means_the_zone_is_absent(self):
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZTYPE*2?'] = REFUSE
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), [1, 3])

  async def test_a_zone_that_never_answers_fails_the_connect(self):
    """Rather than loading an alarm with a sensor silently missing."""
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZSTT*2?'] = SILENT
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(local.connect(), WAIT)

  async def test_connecting_a_connected_panel_again_leaves_nothing_running(self):
    """End the previous listener and session when connecting an already connected object."""
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)
    first = local._listen_task

    await asyncio.wait_for(local.connect(), WAIT)
    second = local._listen_task
    await asyncio.wait_for(local.disconnect(), WAIT)
    await _wait(lambda: not self.panel.open_sessions)

    await _wait(lambda: first.done() and second.done())
    self.assertEqual(len(self.panel.sessions), 2)
    self.assertIn('DCN', self.panel.sessions[0].received)
    self.assert_one_session_at_a_time()

  async def test_a_connection_lost_during_discovery_is_reported_as_such(self):
    """Preserve the connection-loss reason and stop discovery instead of retrying a dead session."""
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZLBL*3?'] = CLOSE
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    with self.assertRaises(CannotConnectError) as caught:
      await asyncio.wait_for(local.connect(), WAIT)

    self.assertNotIn('Not connected', str(caught.exception))
    self.assertEqual(local._left_out, [])

  async def test_a_panel_answering_no_zone_query_fails_the_connect_quickly(self):
    """Fail discovery after two unanswered attempts without waiting for every zone to time out."""
    self.panel.zones = set(range(1, 51))
    for zone in range(1, 51):
      self.panel.rules[f'ZTYPE*{zone}?'] = SILENT
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234', concurrency=4)

    started = time.monotonic()
    with patch_timing(COMMAND_TIMEOUT=0.5):
      with self.assertRaises(CannotConnectError):
        await asyncio.wait_for(local.connect(), 20)
    elapsed = time.monotonic() - started

    # Two command timeouts (1 s) plus handshake and teardown. Every zone query
    # timing out before the first retry would take 50/4 x 0.5 s = 6 s or more.
    self.assertLess(elapsed, 4.0, f'took {elapsed:.2f}s')
    await _until(lambda: not self.panel.open_sessions, timeout=1,
                 what='the panel to see the session closed')

  async def test_a_zone_status_refused_once_does_not_fail_the_connect(self):
    """Recover from one refused zone status so a transient refusal does not block setup."""
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZSTT*2?'] = [REFUSE]
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), [1, 2, 3])

  async def test_a_zone_whose_status_is_always_refused_is_left_out_and_reported(self):
    """Omit and report the unreadable zone so the rest of the panel can connect without invented status."""
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZSTT*2?'] = REFUSE
    supervisor = self.supervisor()

    await asyncio.wait_for(supervisor.start(), WAIT)
    await _until(lambda: supervisor.errors, timeout=1, what='the report')

    self.assertEqual(sorted(supervisor.panel.zones), [1, 3])
    self.assertEqual([str(e) for e in supervisor.errors],
                     ['Zone 2 left out: the panel refused its status'])
    self.assertEqual(len(self.panel.sessions), 1)

  async def test_a_refused_detail_gets_a_placeholder_and_is_reported(self):
    """Report a refused detail and its placeholder instead of silently treating it as real data."""
    self.panel.zones = {1, 2, 3}
    self.panel.rules['ZAREA&*2?'] = REFUSE
    supervisor = self.supervisor()

    await asyncio.wait_for(supervisor.start(), WAIT)
    await _until(lambda: supervisor.errors, timeout=1, what='the report')

    self.assertEqual(sorted(supervisor.panel.zones), [1, 2, 3])
    self.assertEqual(supervisor.panel.zones[2].groups, [])
    self.assertEqual([str(e) for e in supervisor.errors],
                     ['The panel refused ZAREA&*2?; using a placeholder'])

  async def test_a_push_sent_before_a_status_reply_does_not_overwrite_it(self):
    """Keep the newer discovery status when an older queued push is handled after connect()."""
    self.panel.zones = {1, 2}
    self.panel.statuses[1] = 'O---'
    self.panel.push_before['ZSTT*1?'] = 'ZSTT1=----'
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)
    await asyncio.sleep(0.2)

    self.assertTrue(local.zones[1].triggered)

  async def test_a_label_in_another_encoding_does_not_lose_the_zone(self):
    """Keep a zone with an undecodable label by replacing invalid characters in its name.

    Captured panel frames include Latin-1 labels, while a consumer may use UTF-8.
    """
    self.panel.zones = {1, 2, 3}
    self.panel.encoding = 'latin-1'
    self.panel.labels = {2: 'Entr\xe9e'}
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')

    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), [1, 2, 3])
    self.assertEqual(local.zones[2].name, 'Entr\ufffde')

  async def test_a_512_zone_panel_is_discovered_without_starving_the_keep_alive(self):
    """Finish large-panel discovery while allowing keep-alive commands to run.

    Include unused slots, a lost detail and status pushes; require bounded
    completion with no discovery tasks left running.
    """
    self.panel.panel_type = 'RP432MP'  # LightSys+: 512 zones, 32 partitions
    self.panel.zones = set(range(1, 513, 7))
    self.panel.reply_delay = 0.001
    self.panel.rules['ZLBL*8?'] = [SILENT, SILENT]
    self.panel.push_before['ZTYPE*200?'] = 'ZSTT1=O---'
    self.panel.push_before['ZSTT*400?'] = 'PSTT1=E----'
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234', concurrency=4)
    errors = []

    async def _collect(error):
      errors.append(error)

    local.add_error_handler(_collect)
    started = time.monotonic()
    with patch_timing(COMMAND_TIMEOUT=1.0):
      await asyncio.wait_for(local.connect(), 60)
    elapsed = time.monotonic() - started
    self.addAsyncCleanup(local.disconnect)
    await asyncio.sleep(0.1)

    self.assertEqual(sorted(local.zones), sorted(self.panel.zones))
    self.assertEqual(local.zones[8].name, 'Zone 8')
    self.assertEqual([str(e) for e in errors], ['No answer to ZLBL*8?; using a placeholder'])
    self.assertLess(elapsed, 30)
    self.assertEqual(_discovery_tasks(), [])

  async def test_cancelling_a_512_zone_discovery_leaves_nothing_behind(self):
    """Cancel large-panel discovery without leaving its tasks or session running."""
    self.panel.panel_type = 'RP432MP'
    self.panel.zones = set(range(1, 513, 7))
    self.panel.reply_delay = 0.001
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234', concurrency=4)

    connecting = asyncio.create_task(local.connect())
    await _wait(lambda: len(self.panel.sessions) == 1
                and len(self.panel.sessions[0].received) > 300)
    connecting.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await connecting
    await _wait(lambda: not self.panel.open_sessions)

    self.assertEqual(_discovery_tasks(), [])
    self.assertEqual(len(self.panel.sessions), 1)

  async def test_out_of_order_replies_and_stray_errors_reach_the_right_caller(self):
    """A reply must not answer a command it does not belong to, end to end.

    Replies arrive in random order, some behind an unrelated id-less N05,
    at the default concurrency of 4. Every zone must come back with its own
    label.
    """
    self.panel.zones = set(range(1, 51))
    self.panel.scramble = random.Random(20260916)
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234', concurrency=4)

    with patch_timing(COMMAND_TIMEOUT=1.0):
      await asyncio.wait_for(local.connect(), 30)
    self.addAsyncCleanup(local.disconnect)

    self.assertEqual(sorted(local.zones), list(range(1, 51)))
    wrong = {i: z.name for i, z in local.zones.items() if z.name != f'Zone {i}'}
    self.assertEqual(wrong, {}, 'Deliver each out-of-order reply to the command that owns its ID.')


class RecoveryTest(ScriptedPanelTestCase):
  """A lost session must come back on its own, one session at a time."""

  async def _connected_supervisor(self, **kwargs):
    supervisor = self.supervisor(**kwargs)
    await asyncio.wait_for(supervisor.start(), WAIT)
    return supervisor

  async def _recovered(self, supervisor, sessions):
    await _until(lambda: len(self.panel.sessions) >= sessions and supervisor.ready.is_set(),
                 what=f'recovery to session {sessions}')

  async def test_a_link_that_goes_silent_is_given_up_and_reconnected(self):
    """A connection whose packets are silently dropped must be given up on."""
    supervisor = await self._connected_supervisor()

    self.panel.go_silent()
    await self._recovered(supervisor, 2)

    self.assertEqual(supervisor.reloads, 1)
    self.assertTrue(any('closing the connection' in str(e) for e in supervisor.errors))
    self.assert_one_session_at_a_time()

  async def test_the_panel_closing_the_connection_reconnects(self):
    """A graceful FIN surfaces as IncompleteReadError, never seen live."""
    supervisor = await self._connected_supervisor()

    self.panel.close_sessions()
    await self._recovered(supervisor, 2)

    self.assertTrue(any(isinstance(e, asyncio.IncompleteReadError) for e in _losses(supervisor)),
                    supervisor.errors)
    self.assert_one_session_at_a_time()

  async def test_the_panel_resetting_the_connection_reconnects(self):
    supervisor = await self._connected_supervisor()

    self.panel.reset_sessions()
    await self._recovered(supervisor, 2)

    self.assert_one_session_at_a_time()

  async def test_arming_after_a_reconnect_reaches_the_panel(self):
    supervisor = await self._connected_supervisor()
    self.panel.close_sessions()
    await self._recovered(supervisor, 2)

    self.assertTrue(await asyncio.wait_for(supervisor.panel.arm(1), WAIT))
    await _until(lambda: supervisor.panel.partitions[1].armed, timeout=1,
                 what='the pushed armed status')
    self.assertIn('ARM=1', self.panel.sessions[1].received)

  async def test_concurrency_1_connects_keeps_alive_and_recovers(self):
    """Keep discovery, keep-alive and recovery working with one command slot."""
    supervisor = await self._connected_supervisor(concurrency=1)
    await asyncio.sleep(0.5)  # several keep-alives

    self.panel.go_silent()
    await self._recovered(supervisor, 2)

    self.assertEqual(supervisor.reloads, 1)
    self.assert_one_session_at_a_time()

  async def test_a_consumer_that_reloads_only_on_a_reset_still_recovers(self):
    """Deliver connection losses to consumers that reconnect only on ConnectionResetError."""
    supervisor = await self._connected_supervisor(lost=ConnectionResetError)

    self.panel.close_sessions()
    await self._recovered(supervisor, 2)

    self.assertEqual(supervisor.reloads, 1)

  async def test_a_desynced_session_is_given_up_quickly(self):
    """Close a session with unreadable frames before waiting for three keep-alive timeouts."""
    supervisor = await self._connected_supervisor()

    # Long enough that the keep-alive cannot be what gives up on it.
    with patch_timing(COMMAND_TIMEOUT=5.0, KEEP_ALIVE_INTERVAL=5.0):
      self.panel.desync_sessions()
      self.panel.push('ZSTT1=O---')
      self.panel.push('ZSTT1=----')
      await self._recovered(supervisor, 2)

    self.assertTrue(any('unreadable frames in a row' in str(e) for e in supervisor.errors),
                    supervisor.errors)
    self.assert_one_session_at_a_time()

  @unittest.skipIf(sys.platform == 'win32',
                   "asyncio's proactor transport shuts a socket down (FIN) before "
                   "closing it, so a reset cannot be forced from the server side")
  async def test_a_tcp_reset_is_observed_as_a_reset(self):
    """Verify the scripted reset reaches the client as a TCP reset rather than a graceful close."""
    supervisor = await self._connected_supervisor()

    self.panel.reset_sessions()
    await self._recovered(supervisor, 2)

    self.assertTrue(any(isinstance(e, ConnectionResetError) for e in _losses(supervisor)),
                    f'no reset was observed: {supervisor.errors}')

  async def test_a_panel_dropping_the_socket_during_disconnect_is_not_a_loss(self):
    """Treat a panel close during DCN as deliberate so consumers do not reload during shutdown."""
    self.panel.rules['DCN'] = CLOSE
    supervisor = await self._connected_supervisor()
    local = supervisor.panel

    await asyncio.wait_for(local.disconnect(), WAIT)
    await asyncio.sleep(0.5)

    self.assertEqual(supervisor.reloads, 0)
    self.assertFalse(any(isinstance(e, (ConnectionLostError, OSError, EOFError))
                         for e in supervisor.errors), supervisor.errors)
    supervisor.panel = None

  async def test_a_late_error_reply_does_not_fail_a_later_command(self):
    """Hold the unanswered command ID so a late refusal cannot fail an unrelated command.

    A refusal has no query key that could distinguish it by content.
    """
    local = RiscoLocal('127.0.0.1', self.panel.port, '1234')
    await asyncio.wait_for(local.connect(), WAIT)
    self.addAsyncCleanup(local.disconnect)
    self.panel.late_delay = 0.6
    self.panel.rules['ZLBL*1?'] = [LATE_REFUSE]  # N05 at 0.6 s
    self.panel.rules['ZLBL*2?'] = [SILENT]

    with patch_timing(COMMAND_TIMEOUT=0.4):
      with self.assertRaises(CommunicationError):
        await asyncio.wait_for(local._rs.send_result_command('ZLBL*1?'), WAIT)
      # The other ids have come and gone; the next command would get this one.
      local._rs._cmd_id = self.panel.open_sessions[0].ids['ZLBL*1?'] - 1

      # Waiting from 0.4 s to 0.8 s, across the late N05. Only its own
      # timeout may end it, not a refusal meant for the command before.
      with self.assertRaises(CommunicationError):
        await asyncio.wait_for(local._rs.send_result_command('ZLBL*2?'), WAIT)

  async def test_clock_refusals_with_a_command_id_keep_the_session(self):
    """The panel is answering - for example in programming mode - so the
    keep-alive must not tear the session down and re-initialise it."""
    self.panel.rules['CLOCK'] = REFUSE
    supervisor = await self._connected_supervisor()

    await asyncio.sleep(1.0)

    self.assertGreater(self.panel.sessions[0].received.count('CLOCK'), 5)
    self.assertEqual(len(self.panel.sessions), 1)
    self.assertEqual(supervisor.reloads, 0)

  async def test_reconnect_leaves_the_panel_time_to_reset(self):
    supervisor = await self._connected_supervisor()

    self.panel.close_sessions()
    await self._recovered(supervisor, 2)

    gap, = self.reconnect_gaps()
    self.assertGreaterEqual(gap, RECONNECT_DELAY - SLACK)

  async def test_error_replies_to_every_clock_back_off_instead_of_storming(self):
    """Id-less CLOCK refusals must make reconnects back off, not storm.

    Each session now dies after three failed keep-alives. A panel stuck like
    that must not be re-initialised every few seconds: the reconnect delay
    doubles with each short session, up to the cap.
    """
    self.panel.rules['CLOCK'] = REFUSE_NO_ID
    supervisor = await self._connected_supervisor()

    await _until(lambda: len(self.panel.sessions) >= 6, timeout=30,
                 what='six sessions')

    gaps = self.reconnect_gaps()[:5]
    expected = [min(RECONNECT_DELAY * 2 ** n, MAX_RECONNECT_DELAY) for n in range(1, 6)]
    for gap, minimum in zip(gaps, expected):
      self.assertGreaterEqual(gap, minimum - SLACK, f'gaps {gaps}, expected at least {expected}')
    self.assert_one_session_at_a_time()

  async def test_a_session_that_survives_resets_the_backoff(self):
    self.panel.rules['CLOCK'] = REFUSE_NO_ID
    supervisor = await self._connected_supervisor()
    await _until(lambda: len(self.panel.sessions) >= 3, timeout=10, what='three sessions')

    del self.panel.rules['CLOCK']
    # Let a session outlive STABLE_SESSION (2s here), then drop it.
    await _until(lambda: supervisor.ready.is_set(), what='a stable session')
    stable = len(self.panel.sessions)
    await asyncio.sleep(2.3)
    self.panel.close_sessions()
    await self._recovered(supervisor, stable + 1)

    gap = self.reconnect_gaps()[-1]
    self.assertLess(gap, 2 * RECONNECT_DELAY, 'Reset reconnect back-off after a stable session.')
    self.assertGreaterEqual(gap, RECONNECT_DELAY - SLACK)


if __name__ == '__main__':
  unittest.main()
