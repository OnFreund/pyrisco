"""Tests for RiscoLocal - connection lifecycle, discovery and push handling.

These cover failure modes seen on a live LightSYS 2 (RP432M, fw 6.07):
initialisation that half-completes and reports success, a socket left open
when setup fails, a listener that never exits on a clean disconnect, and
pushed status for an id that was never created.
"""

import asyncio
import unittest

from helpers_local import (
    LOOP_TIMEOUT,
    CommunicationError,
    ConnectionLostError,
    FakeSocket,
    legacy_panel_responses,
    panel_responses,
    settle,
)
from pyrisco.common import CannotConnectError, OperationError
from pyrisco.local import risco_socket
from pyrisco.local.risco_local import RiscoLocal

TIMEOUT = CommunicationError('Timeout in command: ZTYPE*4?')


def _local(fake):
  panel = RiscoLocal('host', 1000, '1234')
  panel._rs = fake
  return panel


def _push(body, seq):
  push = risco_socket._Push(body)
  push.seq = seq
  return push


def _collect(sink):
  """Collect errors through the coroutine callback required by add_error_handler."""
  async def _handler(error):
    sink.append(error)
  return _handler


class ConnectCleanupTest(unittest.IsolatedAsyncioTestCase):
  """connect() must not leave a session behind when init fails."""

  async def test_successful_connect_builds_everything(self):
    fake = FakeSocket(panel_responses(zones=(1, 4), partitions=(1, 2)))
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [1, 4])
    self.assertEqual(sorted(panel.partitions), [1, 2])
    self.assertIsNotNone(panel.system)
    self.assertIsNotNone(panel._listen_task)
    self.assertEqual(fake.disconnect_calls, 0)

  async def test_rejected_init_command_disconnects_and_raises_cannot_connect(self):
    """A refused FSVER? must close the session rather than leave it open.

    A session left open makes the panel refuse new connections.
    """
    fake = FakeSocket(panel_responses(), errors={'FSVER?'})
    panel = _local(fake)

    with self.assertRaises(CannotConnectError) as caught:
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    self.assertIsInstance(caught.exception.__cause__, OperationError)
    self.assertIn('N05', str(caught.exception),
                  'Include the refusal reason because Home Assistant logs only the message.')
    self.assertEqual(fake.disconnect_calls, 1, 'Close the session after setup fails.')
    self.assertIsNone(panel._listen_task)

  async def test_unsupported_panel_disconnects_but_keeps_its_own_error(self):
    """An unknown model is not a connection problem, so it is not retried as one."""
    responses = panel_responses()
    responses['PNLCNF'] = 'RPXXXX'
    fake = FakeSocket(responses)
    panel = _local(fake)

    with self.assertRaises(KeyError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    self.assertEqual(fake.disconnect_calls, 1, 'Close the session after setup fails.')

  async def test_a_failing_cleanup_does_not_hide_the_connect_error(self):
    fake = FakeSocket(panel_responses(), errors={'FSVER?'})
    fake.disconnect_error = OSError('close failed')
    panel = _local(fake)

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

  async def test_cancelled_init_aborts_the_session(self):
    """HA cancels setup on shutdown; the session must still close."""
    fake = FakeSocket(panel_responses())
    fake.block_on = 'PNLSERD'
    panel = _local(fake)

    task = asyncio.create_task(panel.connect())
    await asyncio.wait_for(fake.blocked.wait(), LOOP_TIMEOUT)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task

    self.assertEqual(fake.abort_calls, 1, 'Abort the session when connect is cancelled.')
    self.assertIsNone(panel._listen_task)


class LegacyPanelTest(unittest.IsolatedAsyncioTestCase):
  """Panels whose type does not start with RP take a shorter path."""

  async def test_a_legacy_panel_connects_without_the_newer_queries(self):
    fake = FakeSocket(legacy_panel_responses(zones=(2, 5), partitions=(1,)))
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [2, 5])
    self.assertEqual(panel.zones[5].groups, [])
    for command in fake.sent:
      self.assertFalse(command.startswith(('FSVER', 'ZLNKTYP', 'ZAREA')), command)


class HalfInitialisedPanelTest(unittest.IsolatedAsyncioTestCase):
  """Init must fail loudly rather than hand back a half-built panel."""

  async def test_rejected_system_status_fails_setup(self):
    """A refused system status fails setup, rather than a panel without one."""
    fake = FakeSocket(panel_responses(), errors={'SSTT?'})
    panel = _local(fake)

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    self.assertIsNone(panel._listen_task)
    self.assertEqual(fake.disconnect_calls, 1)

  async def test_no_partitions_at_all_fails_setup(self):
    """A panel always has at least one partition; none means init failed."""
    fake = FakeSocket(panel_responses(partitions=()))
    panel = _local(fake)

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    self.assertEqual(fake.disconnect_calls, 1)

  async def test_unused_partition_slots_are_tolerated(self):
    fake = FakeSocket(panel_responses(partitions=(2,)))
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.partitions), [2])


class DiscoveryTest(unittest.IsolatedAsyncioTestCase):
  """A refused query means "not there"; a lost answer means "don't know"."""

  async def test_a_refused_zone_query_means_the_zone_is_absent(self):
    """Deliberately unchanged: some models answer an unused slot with an error."""
    fake = FakeSocket(panel_responses(zones=(1, 4)), errors={'ZTYPE*4?'})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [1])

  async def test_a_refused_label_keeps_the_zone_under_a_placeholder(self):
    """Retain a confirmed zone with a placeholder name if its label is refused.

    A missing label must not silently remove an alarm sensor from the consumer.
    """
    fake = FakeSocket(panel_responses(zones=(1, 4)), errors={'ZLBL*4?'})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [1, 4])
    self.assertEqual(panel.zones[4].name, 'Zone 4')

  async def test_refused_zone_details_fall_back_without_dropping_the_zone(self):
    fake = FakeSocket(panel_responses(zones=(4,)),
                      errors={'ZLNKTYP4?', 'ZPART&*4?', 'ZAREA&*4?'})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    zone = panel.zones[4]
    self.assertEqual(zone.name, 'Zone 4')
    self.assertEqual(zone.partitions, [])
    self.assertEqual(zone.groups, [])

  async def test_a_zone_whose_status_is_refused_twice_is_left_out_and_reported(self):
    """Omit and report a zone whose status is refused twice while connecting the remaining panel.

    Inventing a clear status could hide an alarm or skip an unbypass. Failing
    the entire connection would make every other zone unavailable too.
    """
    fake = FakeSocket(panel_responses(zones=(1, 4)), errors={'ZSTT*4?'})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    errors = []
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(sorted(panel.zones), [1])
    self.assertEqual(fake.sent.count('ZSTT*4?'), 2, 'Retry a refused zone status once before omitting the zone.')
    self.assertEqual(fake.disconnect_calls, 0)
    self.assertEqual(len(errors), 1, errors)
    self.assertIn('Zone 4 left out', str(errors[0]))

  async def test_a_left_out_zone_is_reported_to_a_handler_added_before_connect(self):
    fake = FakeSocket(panel_responses(zones=(1, 4)), errors={'ZSTT*4?'})
    panel = _local(fake)
    errors = []
    panel.add_error_handler(_collect(errors))

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    await settle()
    panel.add_error_handler(_collect([]))
    await settle()

    self.assertEqual(len(errors), 1, errors)

  async def test_a_later_connect_does_not_report_an_earlier_zone_again(self):
    fake = FakeSocket(panel_responses(zones=(1, 4)),
                      failures={'ZSTT*4?': [OperationError('N05'), OperationError('N05')]})
    panel = _local(fake)
    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    await panel.disconnect()

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    errors = []
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(sorted(panel.zones), [1, 4])
    self.assertEqual(errors, [])

  async def test_cancelling_connect_during_discovery_closes_the_session(self):
    """Abort the session when connect() is cancelled during active discovery."""
    fake = FakeSocket(panel_responses(zones=(1, 2, 3)))
    fake.block_on = 'ZLBL*3?'
    panel = _local(fake)
    before = asyncio.all_tasks()

    task = asyncio.create_task(panel.connect())
    await asyncio.wait_for(fake.blocked.wait(), LOOP_TIMEOUT)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    await settle()

    self.assertEqual(fake.abort_calls, 1)
    left = {t for t in asyncio.all_tasks() - before if not t.done()} - {asyncio.current_task()}
    self.assertEqual(left, set())

  async def test_a_zone_status_refused_once_is_asked_again(self):
    """Retry a refused zone status once so a transient refusal does not prevent setup."""
    fake = FakeSocket(panel_responses(zones=(1, 4)),
                      failures={'ZSTT*4?': [OperationError('cmd_id: 9, Risco error: N05')]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [1, 4])

  async def test_a_zone_type_refused_once_is_asked_again(self):
    """Retry a refused zone type once before treating the slot as unused."""
    fake = FakeSocket(panel_responses(zones=(1, 4)),
                      failures={'ZTYPE*4?': [OperationError('cmd_id: 9, Risco error: N05')]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [1, 4])

  async def test_a_system_status_refused_once_is_asked_again(self):
    """Retry a refused system status once so a transient refusal does not fail connect()."""
    fake = FakeSocket(panel_responses(zones=(1,)),
                      failures={'SSTT?': [OperationError('cmd_id: 9, Risco error: N05')]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertIsNotNone(panel.system)

  async def test_a_partition_status_refused_once_is_asked_again(self):
    """Retry a refused partition status once so an existing partition is not omitted."""
    fake = FakeSocket(panel_responses(zones=(1,), partitions=(1, 2)),
                      failures={'PSTT1?': [OperationError('cmd_id: 7, Risco error: N05')]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.partitions), [1, 2])

  async def test_a_detail_the_panel_never_answers_gets_a_placeholder(self):
    """Use a reported placeholder for an unanswered detail so it cannot block the entire setup."""
    fake = FakeSocket(panel_responses(zones=(1, 2)),
                      failures={'ZAREA&*2?': [TIMEOUT, TIMEOUT], 'ZLBL*1?': [TIMEOUT]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    errors = []
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(sorted(panel.zones), [1, 2])
    self.assertEqual(panel.zones[1].name, 'Zone 1', 'Retry the detail once before using its answer.')
    self.assertEqual(panel.zones[2].groups, [])
    self.assertEqual([str(e) for e in errors], ['No answer to ZAREA&*2?; using a placeholder'])

  async def test_a_retried_zone_reports_only_what_its_last_attempt_used(self):
    """Discard placeholder notices from a failed attempt if the successful retry reads the detail."""
    fake = FakeSocket(panel_responses(zones=(1,)), failures={
        'ZLNKTYP1?': [OperationError('cmd_id: 3, Risco error: N05'), None],
        'ZSTT*1?': [TIMEOUT, None],
    })
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    errors = []
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(sorted(panel.zones), [1])
    self.assertEqual(fake.sent.count('ZLNKTYP1?'), 2, 'Retry the zone after losing its first answer.')
    self.assertEqual(errors, [])

  async def test_the_first_failure_is_the_one_reported(self):
    """Report the first discovery failure in time rather than the lowest-numbered failed object."""
    fake = FakeSocket(panel_responses(zones=(1, 2)))
    original = fake.send_command
    zone_2_failed = asyncio.Event()

    async def _send(command, force_encryption=False, timeout=None):
      if command == 'ZTYPE*1?':
        # Zone 1 fails too, but only once zone 2 has - before anything is
        # cancelled.
        await zone_2_failed.wait()
        raise CommunicationError('zone 1, second')
      if command == 'ZTYPE*2?':
        zone_2_failed.set()
        raise RuntimeError('zone 2, first')
      return await original(command, force_encryption, timeout)

    fake.send_command = _send
    panel = _local(fake)

    with self.assertRaises(RuntimeError) as caught:
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.assertIn('zone 2, first', str(caught.exception))

  async def test_connecting_a_connected_panel_does_not_leave_its_listener(self):
    """Stop the previous listener before reconnecting so it cannot remain blocked on the old queue."""
    fake = FakeSocket(panel_responses(zones=(1,)))
    panel = _local(fake)
    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    first = panel._listen_task

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    await asyncio.wait_for(panel.disconnect(), LOOP_TIMEOUT)
    await settle()

    self.assertTrue(first.done())
    self.assertEqual(fake.disconnect_calls, 2)

  async def test_a_cancel_while_discovery_is_failing_still_aborts(self):
    """Preserve caller cancellation even when a discovery task fails at the same time.

    Abort immediately and propagate the cancellation instead of converting
    it to CannotConnectError or waiting for graceful cleanup.
    """
    fake = FakeSocket(panel_responses(zones=(1, 2, 3)))
    panel = _local(fake)
    original = fake.send_command
    connecting = None

    async def _send(command, force_encryption=False, timeout=None):
      if command == 'ZTYPE*1?':
        asyncio.get_running_loop().call_soon(connecting.cancel)
        raise CommunicationError('Timeout in command: ZTYPE*1?')
      if command.startswith('ZTYPE*'):
        await asyncio.sleep(3600)
      return await original(command, force_encryption, timeout)

    fake.send_command = _send
    connecting = asyncio.create_task(panel.connect())

    with self.assertRaises(asyncio.CancelledError):
      await asyncio.wait_for(connecting, LOOP_TIMEOUT)
    self.assertEqual(fake.abort_calls, 1)
    self.assertEqual(fake.disconnect_calls, 0)

  async def test_a_reply_that_does_not_parse_fails_the_connect_retryably(self):
    """Wrap an unparseable discovery reply as CannotConnectError so consumers can retry setup."""
    responses = panel_responses(zones=(1,))
    responses['ZTYPE*1?'] = 'x'
    fake = FakeSocket(responses)
    panel = _local(fake)

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.assertEqual(fake.disconnect_calls, 1)

  async def test_discovery_stops_at_the_first_failure_it_cannot_recover(self):
    """Cancel remaining discovery reads when one fails irrecoverably so setup fails promptly."""
    fake = FakeSocket(panel_responses(zones=(1, 2)),
                      failures={'ZTYPE*2?': [TIMEOUT, TIMEOUT]})
    fake.block_on = 'ZTYPE*40?'
    panel = _local(fake)

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.assertEqual(fake.disconnect_calls, 1)

  async def test_a_refused_partition_label_keeps_the_partition(self):
    fake = FakeSocket(panel_responses(partitions=(1, 2)), errors={'PLBL2?'})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.partitions), [1, 2])
    self.assertEqual(panel.partitions[2].name, 'Partition 2')

  async def test_a_refused_system_label_keeps_the_system(self):
    fake = FakeSocket(panel_responses(), errors={'SYSLBL?'})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(panel.system.name, '')

  async def test_a_lost_zone_answer_is_retried(self):
    fake = FakeSocket(panel_responses(zones=(1, 4)),
                      failures={'ZTYPE*4?': [TIMEOUT]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.zones), [1, 4])

  async def test_a_zone_that_cannot_be_read_twice_fails_the_connect(self):
    """Before, the zone was silently dropped and the entry loaded without it.
    (A lost detail, such as the label, gets a placeholder instead.)"""
    fake = FakeSocket(panel_responses(zones=(1, 4)),
                      failures={'ZSTT*4?': [TIMEOUT, TIMEOUT]})
    panel = _local(fake)

    with self.assertRaises(CannotConnectError) as caught:
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    self.assertIsInstance(caught.exception.__cause__, CommunicationError)
    self.assertEqual(fake.disconnect_calls, 1)

  async def test_a_lost_partition_answer_is_retried(self):
    fake = FakeSocket(panel_responses(partitions=(1, 2)),
                      failures={'PLBL2?': [TIMEOUT]})
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)

    self.assertEqual(sorted(panel.partitions), [1, 2])

  async def test_a_lost_system_answer_fails_the_connect(self):
    fake = FakeSocket(panel_responses(), failures={'SSTT?': [TIMEOUT]})
    panel = _local(fake)

    with self.assertRaises(CannotConnectError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

  async def test_an_unexpected_error_in_discovery_is_not_swallowed(self):
    """An unsupported panel model is not a connection problem; it must not be
    retried as one. (A reply that does not parse is: see above.)"""
    responses = panel_responses(zones=(1,))
    responses['PNLCNF'] = 'RP999'
    fake = FakeSocket(responses)
    panel = _local(fake)

    with self.assertRaises(KeyError):
      await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    self.assertEqual(fake.disconnect_calls, 1)


class SetupErrorTest(unittest.IsolatedAsyncioTestCase):
  """Errors raised while connect() ran are not delivered once it succeeds."""

  async def test_recovered_setup_errors_are_dropped_but_pushes_are_kept(self):
    """Drop recovered setup errors while preserving status pushes queued during discovery."""
    fake = FakeSocket(panel_responses(zones=(1,)))
    fake.queue.put_nowait(OperationError('Risco error: N05'))
    fake.queue.put_nowait(_push('ZSTT1=O---', seq=20))  # after the status read
    fake.queue.put_nowait(CommunicationError('Timeout in command: ZTYPE*9?'))
    fake.reply_seqs['ZSTT*1?'] = 10
    panel = _local(fake)
    errors = []

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(errors, [])
    # The push may be handled before a consumer registers its handlers (on
    # 3.11, wait_for runs connect() in its own task); what must not be lost
    # is the state it carries, which consumers read when they set up.
    self.assertTrue(panel.zones[1].triggered, 'Apply the status push queued during setup.')

  async def test_a_loss_after_the_library_gave_up_says_why(self):
    """Report the library's shutdown reason instead of only the EOF caused by closing the socket."""
    fake = FakeSocket(panel_responses(zones=(1,)))
    panel = _local(fake)
    errors = []
    panel.add_error_handler(_collect(errors))
    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)

    fake.close_reason = 'Keep-alive failed 3 times in a row'
    fake.queue.put_nowait(asyncio.IncompleteReadError(b'', None))
    await settle()

    self.assertEqual(str(errors[-1]), 'Connection lost: Keep-alive failed 3 times in a row')
    self.assertIsInstance(errors[-1].__cause__, asyncio.IncompleteReadError)

  async def test_a_lost_connection_is_also_a_connection_reset(self):
    """Preserve ConnectionResetError compatibility so existing consumers still reconnect on loss."""
    self.assertTrue(issubclass(ConnectionLostError, ConnectionResetError))
    self.assertTrue(issubclass(ConnectionLostError, CommunicationError))
    self.assertEqual(str(ConnectionLostError('Connection lost: EOFError')),
                     'Connection lost: EOFError')

  async def test_a_push_older_than_the_status_read_at_connect_is_ignored(self):
    """Ignore an older queued push so it cannot overwrite the status read during discovery."""
    responses = panel_responses(zones=(1,))
    responses['ZSTT*1?'] = 'O---'
    responses['PSTT1?'] = 'EA---'
    responses['SSTT?'] = 'B---'
    fake = FakeSocket(responses)
    fake.reply_seqs.update({'ZSTT*1?': 10, 'PSTT1?': 11, 'SSTT?': 12})
    for body in ('ZSTT1=----', 'PSTT1=E----', 'SSTT=----'):
      fake.queue.put_nowait(_push(body, seq=5))
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    await settle()

    self.assertTrue(panel.zones[1].triggered)
    self.assertTrue(panel.partitions[1].armed)
    self.assertEqual(panel.system._status, 'B---')

  async def test_a_push_newer_than_the_status_read_at_connect_is_applied(self):
    fake = FakeSocket(panel_responses(zones=(1,)))
    fake.reply_seqs['ZSTT*1?'] = 10
    fake.queue.put_nowait(_push('ZSTT1=O---', seq=11))
    panel = _local(fake)

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    await settle()

    self.assertTrue(panel.zones[1].triggered)

  async def test_a_connection_loss_during_setup_is_not_dropped(self):
    fake = FakeSocket(panel_responses(zones=(1,)))
    fake.queue.put_nowait(ConnectionResetError())
    panel = _local(fake)
    errors = []
    panel.add_error_handler(_collect(errors))

    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    await settle()

    self.assertEqual(fake.disconnect_calls, 1, 'Handle a connection loss queued during setup.')
    self.assertIsInstance(errors[0], ConnectionLostError)
    self.assertIsInstance(errors[0].__cause__, ConnectionResetError)


class PushHandlingTest(unittest.IsolatedAsyncioTestCase):
  """A push for an id that was never created must not kill the listener."""

  async def _connected(self, zones=(1,), partitions=(1,)):
    fake = FakeSocket(panel_responses(zones=zones, partitions=partitions))
    panel = _local(fake)
    await asyncio.wait_for(panel.connect(), LOOP_TIMEOUT)
    self.addAsyncCleanup(panel.disconnect)
    errors = []
    panel.add_error_handler(_collect(errors))
    return panel, fake, errors

  async def test_unknown_zone_push_is_reported_once(self):
    """A push for an unknown zone is reported, not raised as KeyError."""
    panel, fake, errors = await self._connected()

    for _ in range(3):
      fake.queue.put_nowait('ZSTT4=O---')
    await settle()

    self.assertEqual(len(errors), 1, f'expected one report, got {errors}')
    self.assertIsInstance(errors[0], OperationError)
    self.assertIn('zone: 4', str(errors[0]))
    self.assertFalse(panel._listen_task.done(), 'Keep the listener running after an unknown-zone push.')

  async def test_unknown_partition_push_is_reported_once(self):
    """A push for an unknown partition is reported, not raised as KeyError."""
    panel, fake, errors = await self._connected(partitions=(2,))

    fake.queue.put_nowait('PSTT1=E----')
    fake.queue.put_nowait('PSTT1=E----')
    await settle()

    self.assertEqual(len(errors), 1, f'expected one report, got {errors}')
    self.assertIn('partition: 1', str(errors[0]))

  async def test_known_zone_push_updates_and_notifies(self):
    panel, fake, errors = await self._connected()
    seen = []

    async def _zone_handler(zone_id, zone):
      seen.append(zone_id)

    panel.add_zone_handler(_zone_handler)
    fake.queue.put_nowait('ZSTT1=O---')
    await settle()

    self.assertEqual(seen, [1])
    self.assertTrue(panel.zones[1].triggered)
    self.assertEqual(errors, [])


class HandlerErrorTest(unittest.IsolatedAsyncioTestCase):
  """Log handler failures and keep delivering events to the other handlers."""

  async def test_a_handler_that_raises_is_logged(self):
    panel = _local(FakeSocket())

    async def _broken(error):
      raise RuntimeError('handler bug')

    panel.add_error_handler(_broken)
    with self.assertLogs('pyrisco.local.risco_local', 'ERROR') as logs:
      panel._error(OperationError('N05'))
      await settle()

    self.assertIn('handler bug', '\n'.join(logs.output))

  async def test_a_handler_that_is_not_a_coroutine_is_logged(self):
    panel = _local(FakeSocket())
    panel.add_error_handler(lambda error: None)

    with self.assertLogs('pyrisco.local.risco_local', 'ERROR'):
      panel._error(OperationError('N05'))
      await settle()

  async def test_other_handlers_still_run(self):
    panel = _local(FakeSocket())
    seen = []

    async def _broken(error):
      raise RuntimeError('handler bug')

    panel.add_error_handler(_broken)
    panel.add_error_handler(_collect(seen))
    with self.assertLogs('pyrisco.local.risco_local', 'ERROR'):
      panel._error(OperationError('N05'))
      await settle()

    self.assertEqual(len(seen), 1)


class UndeliveredErrorTest(unittest.IsolatedAsyncioTestCase):

  async def test_a_loss_before_any_handler_is_added_reaches_the_first_one(self):
    """Retain a loss until the first error handler is registered.

    The listener may process it between connect() finishing and the caller
    adding a handler; dropping it would leave the consumer unaware of the loss.
    """
    fake = FakeSocket(panel_responses(zones=(1,)))
    panel = _local(fake)
    await asyncio.wait_for(asyncio.create_task(panel.connect()), LOOP_TIMEOUT)
    fake.queue.put_nowait(ConnectionResetError())
    await settle()
    self.assertEqual(fake.disconnect_calls, 1)

    errors = []
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(len(errors), 1, errors)
    self.assertIsInstance(errors[0], ConnectionLostError)

  async def test_undelivered_errors_are_bounded(self):
    panel = _local(FakeSocket())
    for n in range(30):
      panel._error(OperationError(f'N05 #{n}'))

    errors = []
    panel.add_error_handler(_collect(errors))
    await settle()

    self.assertEqual(len(errors), 20)
    self.assertEqual(str(errors[-1]), 'N05 #29')

  async def test_errors_go_straight_to_a_registered_handler(self):
    panel = _local(FakeSocket())
    errors = []
    panel.add_error_handler(_collect(errors))

    panel._error(OperationError('N05'))
    await settle()

    self.assertEqual(len(errors), 1)
    self.assertEqual(len(panel._undelivered), 0)

  async def test_retained_errors_reach_only_the_handler_that_was_added(self):
    """A handler registered later did not miss the retained errors.

    Delivery runs in its own task, so a second handler added in the same
    moment must not receive the backlog of the first.
    """
    panel = _local(FakeSocket())
    panel._error(OperationError('N05'))

    first = []
    second = []
    panel.add_error_handler(_collect(first))
    panel.add_error_handler(_collect(second))
    await settle()

    self.assertEqual(len(first), 1)
    self.assertEqual(second, [])

  async def test_retained_errors_survive_removing_the_handler(self):
    """Removing the handler before the delivery runs must not discard them."""
    panel = _local(FakeSocket())
    panel._error(OperationError('N05'))

    errors = []
    remove = panel.add_error_handler(_collect(errors))
    remove()
    await settle()

    self.assertEqual(len(errors), 1)


class ListenerShutdownTest(unittest.IsolatedAsyncioTestCase):
  """A dead connection must end the listener, not spin on it."""

  async def _listener_exits_on(self, error):
    fake = FakeSocket()
    panel = _local(fake)
    errors = []
    panel.add_error_handler(_collect(errors))
    queue = asyncio.Queue()
    queue.put_nowait(error)
    task = asyncio.create_task(panel._listen(queue))
    panel._listen_task = task

    await asyncio.wait_for(task, LOOP_TIMEOUT)
    await settle()

    self.assertFalse(task.cancelled(), 'Let the listener exit normally after reporting the loss.')
    self.assertEqual(fake.disconnect_calls, 1, 'Disconnect when the listener receives a terminal read error.')
    # Normalize every terminal read to one loss type so consumers do not
    # need their own tuple of asyncio and OS exceptions.
    self.assertEqual(len(errors), 1, errors)
    self.assertIsInstance(errors[0], ConnectionLostError)
    self.assertIs(errors[0].__cause__, error)
    self.assertIn(type(error).__name__, str(errors[0]))

  async def test_graceful_eof_exits_listener(self):
    """A clean close - by the panel, or by the keep-alive - is IncompleteReadError."""
    await self._listener_exits_on(asyncio.IncompleteReadError(b'', None))

  async def test_connection_reset_exits_listener(self):
    await self._listener_exits_on(ConnectionResetError())

  async def test_broken_pipe_exits_listener(self):
    await self._listener_exits_on(BrokenPipeError())

  async def test_a_socket_timeout_exits_listener(self):
    """ETIMEDOUT: an OSError, but not a ConnectionError."""
    await self._listener_exits_on(TimeoutError(110, 'Connection timed out'))

  async def test_an_unreadable_stream_exits_listener(self):
    await self._listener_exits_on(asyncio.LimitOverrunError('frame too long', 64))

  async def _listener_survives(self, error):
    fake = FakeSocket()
    panel = _local(fake)
    queue = asyncio.Queue()
    queue.put_nowait(error)
    task = asyncio.create_task(panel._listen(queue))
    panel._listen_task = task
    self.addCleanup(task.cancel)

    await settle(20)

    self.assertFalse(task.done(), f'listener exited on {error!r}')
    self.assertEqual(fake.disconnect_calls, 0)

  async def test_a_refused_command_does_not_exit_listener(self):
    await self._listener_survives(OperationError('Risco error: N05'))

  async def test_a_keep_alive_timeout_does_not_exit_listener(self):
    await self._listener_survives(CommunicationError('Timeout in command: CLOCK'))


class DisconnectTest(unittest.IsolatedAsyncioTestCase):
  """disconnect() must always end the listen task."""

  async def test_failing_socket_disconnect_still_cancels_listen_task(self):
    """If _rs.disconnect() raised, the listen task used to be orphaned.

    It kept reading a dead queue for the life of the process. Home
    Assistant logs the exception and reports the unload as successful, so
    nothing else would ever stop it.
    """
    fake = FakeSocket()
    fake.disconnect_error = OSError('boom')
    panel = _local(fake)
    orphan = asyncio.create_task(asyncio.sleep(3600))
    panel._listen_task = orphan
    self.addCleanup(orphan.cancel)

    with self.assertRaises(OSError):
      await asyncio.wait_for(panel.disconnect(), LOOP_TIMEOUT)

    await asyncio.sleep(0)
    self.assertIsNone(panel._listen_task, 'Clear the listener task reference during disconnect.')
    self.assertTrue(orphan.cancelled(), 'Cancel the listener even if socket disconnect fails.')

  async def test_disconnect_cancels_listen_task(self):
    fake = FakeSocket()
    panel = _local(fake)
    task = asyncio.create_task(asyncio.sleep(3600))
    panel._listen_task = task
    self.addCleanup(task.cancel)

    await asyncio.wait_for(panel.disconnect(), LOOP_TIMEOUT)
    await asyncio.sleep(0)

    self.assertIsNone(panel._listen_task)
    self.assertTrue(task.cancelled())


if __name__ == '__main__':
  unittest.main()
