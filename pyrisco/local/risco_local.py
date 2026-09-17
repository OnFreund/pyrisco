import asyncio
import collections
import copy
import logging
from .const import PANEL_TYPE, PANEL_MODEL, PANEL_FW, MAX_ZONES, MAX_PARTS, MAX_OUTPUTS
from .panels import panel_capabilities
from .partition import Partition
from .zone import Zone
from .system import System
from .risco_socket import READ_FAILURES, RiscoSocket, describe
from pyrisco.common import (
    CannotConnectError, CommunicationError, ConnectionLostError, OperationError, GROUP_ID_TO_NAME)

_LOGGER = logging.getLogger(__name__)

# Errors kept for an error handler that is not registered yet.
MAX_UNDELIVERED_ERRORS = 20

# Own handler tasks until done: asyncio holds only weak references.
# disconnect() leaves them running; consumers own handlers that never return.
_handler_tasks = set()


class RiscoLocal:
  def __init__(self, host, port, code, **kwargs):
    self._rs = RiscoSocket(host, port, code, **kwargs)
    self._panel_capabilities = None
    self._listen_task = None
    self._system_handlers = []
    self._zone_handlers = []
    self._partition_handlers = []
    self._error_handlers = []
    self._default_handlers = []
    self._event_handlers = []
    self._system = None
    self._zones = None
    self._partitions = None
    self._id = None
    self._legacy_panel = False
    self._reported_unknown = set()
    self._left_out = []
    # Bounded error backlog for the first handler registered.
    self._undelivered = collections.deque(maxlen=MAX_UNDELIVERED_ERRORS)
    # Receive order of the status each object was read with at connect.
    self._snapshots = {}

  async def connect(self):
    if self._listen_task is not None:
      # Close the previous session and listener first.
      await self.disconnect()
    self._left_out = []
    self._undelivered.clear()
    self._snapshots = {}
    await self._rs.connect()
    try:
      panel_type = await self._rs.send_result_command("PNLCNF")
      self._legacy_panel = not panel_type.startswith("RP")
      if self._legacy_panel:
        firmware = ""
      else:
        firmware = await self._rs.send_result_command("FSVER?")
      self._panel_capabilities = panel_capabilities(panel_type, firmware)
      self._id = await self._rs.send_result_command("PNLSERD")
      self._zones = await self._init_zones()
      self._partitions = await self._init_partitions()
      self._system = await self._init_system()
      # A usable panel requires a system and at least one partition.
      if self._system is None:
        raise OperationError('Failed to read system status')
      if not self._partitions:
        raise OperationError('Panel reported no partitions')
    except asyncio.CancelledError:
      # Abort synchronously: another cancellation could interrupt awaited cleanup.
      self._rs.abort()
      raise
    except (OperationError, ValueError) as error:
      # An unparseable reply is a communication failure.
      reason = self._rs.close_reason or describe(error)
      await self._disconnect_after_failed_connect()
      raise CannotConnectError(reason) from error
    except Exception:
      # Close the session while preserving unexpected exceptions.
      await self._disconnect_after_failed_connect()
      raise

    self._reported_unknown = set()
    self._discard_setup_errors()
    for error in self._left_out:
      self._error(error)
    self._listen_task = asyncio.create_task(self._listen(self._rs.queue))

  def _discard_setup_errors(self):
    """Discard recovered setup errors; retain pushes and connection loss.
    Keep-alive reports only the first refusal of a run, so a run starting
    during connect() stays unreported until it ends and recurs.
    """
    queue = self._rs.queue
    for _ in range(queue.qsize()):
      item = queue.get_nowait()
      if not isinstance(item, Exception) or isinstance(item, READ_FAILURES):
        queue.put_nowait(item)

  async def _disconnect_after_failed_connect(self):
    try:
      await self._rs.disconnect()
    except Exception:
      # Preserve the connect failure.
      pass

  async def disconnect(self):
    listen_task = self._listen_task
    self._listen_task = None
    try:
      await self._rs.disconnect()
    finally:
      # Always cancel the listener, except when it is this task.
      if listen_task is not None and listen_task is not asyncio.current_task():
        listen_task.cancel()

  def add_error_handler(self, handler):
    remove = RiscoLocal._add_handler(self._error_handlers, handler)
    undelivered = list(self._undelivered)
    self._undelivered.clear()
    for error in undelivered:
      # This handler alone: handlers registered after it did not miss these,
      # and removing it before the delivery runs must not discard them.
      RiscoLocal._call_handlers((handler,), error)
    return remove

  def add_event_handler(self, handler):
    return RiscoLocal._add_handler(self._event_handlers, handler)

  def add_system_handler(self, handler):
    return RiscoLocal._add_handler(self._system_handlers, handler)

  def add_zone_handler(self, handler):
    return RiscoLocal._add_handler(self._zone_handlers, handler)

  def add_partition_handler(self, handler):
    return RiscoLocal._add_handler(self._partition_handlers, handler)

  def add_default_handler(self, handler):
    return RiscoLocal._add_handler(self._default_handlers, handler)

  @property
  def id(self):
    return self._id

  @property
  def zones(self):
    return self._zones

  @property
  def partitions(self):
    return self._partitions

  @property
  def system(self):
    return self._system

  async def disarm(self, partition_id):
    """Disarm a partition."""
    return await self._rs.send_ack_command(f'DISARM={partition_id}')

  async def arm(self, partition_id):
    """Arm a partition."""
    return await self._rs.send_ack_command(f'ARM={partition_id}')

  async def partial_arm(self, partition_id):
    """Partially-arm a partition."""
    return await self._rs.send_ack_command(f'STAY={partition_id}')

  async def group_arm(self, partition_id, group):
    """Arm a specific group on a partition."""
    if isinstance(group, str):
        group = GROUP_ID_TO_NAME.index(group) + 1

    return await self._rs.send_ack_command(f'GARM*{group}={partition_id}')

  async def bypass_zone(self, zone_id, bypass):
    """Bypass or unbypass a zone."""
    if self.zones[zone_id].bypassed != bypass:
      await self._rs.send_ack_command(F'ZBYPAS={zone_id}')

  async def set_time(self, time):
    """Set the time of the panel."""
    formatted_time = time.strftime('%d/%m/%Y %H:%M')
    await self._rs.send_ack_command(F'CLOCK={formatted_time}')

  def _add_handler(handlers, handler):
    handlers.append(handler)
    def _remove():
      handlers.remove(handler)
    return _remove

  async def _init_system(self):
    label = await self._detail('SYSLBL?', '', self._left_out)
    try:
      status, seq = await self._ask_twice('SSTT?')
    except CommunicationError:
      raise
    except OperationError:
      return None
    self._snapshots['system'] = seq
    return System(self, label, status)

  async def _init_partitions(self):
    return await self._get_objects(1, self._panel_capabilities[MAX_PARTS], self._create_partition)

  async def _init_zones(self):
    return await self._get_objects(1, self._panel_capabilities[MAX_ZONES], self._create_zone)

  async def _get_objects(self, min, max, func):
    # Bound object concurrency to command slots so retries run immediately;
    # a silent panel fails after two timeouts, not one per queued object.
    slots = asyncio.Semaphore(self._rs.concurrency)
    failures = []

    async def _attempt(object_id):
      # Publish reports only from the successful object attempt.
      reports = []
      result = await func(object_id, reports)
      self._left_out.extend(reports)
      return result

    async def _read(object_id):
      async with slots:
        try:
          try:
            return await _attempt(object_id)
          except CommunicationError:
            if not self._rs.connected:
              raise  # the connection is gone; asking again cannot help
            # Retry a lost answer once; another failure fails connect, not the object.
            return await _attempt(object_id)
        except Exception as error:
          failures.append(error)
          raise

    # Stop on first failure; preserve caller cancellation even if a child failed.
    tasks = [asyncio.create_task(_read(i)) for i in range(min, min+max)]
    try:
      await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    finally:
      # Cancel and drain all reads so no command is left behind.
      for task in tasks:
        task.cancel()
      await asyncio.gather(*tasks, return_exceptions=True)
    if failures:
      raise failures[0]  # first in time, not task order

    temp = [task.result() for task in tasks]
    return { o.id: o for o in temp if o }

  async def _create_partition(self, partition_id, reports):
    try:
      status, seq = await self._ask_twice(f'PSTT{partition_id}?')
    except CommunicationError:
      raise
    except OperationError:
      # How an unused slot answers on some models.
      return None
    if not 'E' in status:
      return None
    label = await self._detail(f'PLBL{partition_id}?', f'Partition {partition_id}', reports)
    self._snapshots[('partition', partition_id)] = seq
    return Partition(self, partition_id, label, status)

  async def _create_zone(self, zone_id, reports):
    try:
      zone_type = int((await self._ask_twice(f'ZTYPE*{zone_id}?'))[0])
    except CommunicationError:
      raise
    except OperationError:
      # Silently omit: an unused slot and a twice-refused zone look the same.
      return None
    if zone_type == 0:
      return None

    tech = '' if self._legacy_panel else await self._detail(f'ZLNKTYP{zone_id}?', '', reports)
    if tech.strip() == 'N':
      return None
    # Never invent a zone status; report and omit a twice-refused zone.
    try:
      status, seq = await self._ask_twice(f'ZSTT*{zone_id}?')
    except CommunicationError:
      raise
    except OperationError as error:
      left_out = OperationError(f'Zone {zone_id} left out: the panel refused its status')
      left_out.__cause__ = error
      reports.append(left_out)
      return None
    if status.endswith('N'):
      return None

    label = await self._detail(f'ZLBL*{zone_id}?', f'Zone {zone_id}', reports)
    partitions = await self._detail(f'ZPART&*{zone_id}?', '0', reports)
    groups = '0' if self._legacy_panel else await self._detail(f'ZAREA&*{zone_id}?', '0', reports)
    self._snapshots[('zone', zone_id)] = seq
    return Zone(self, zone_id, status, zone_type, label, partitions, groups, tech)

  async def _ask_twice(self, command):
    """Retry a refused status/type once; _get_objects retries lost answers."""
    try:
      return await self._rs.send_status_query(command)
    except CommunicationError:
      raise
    except OperationError:
      return await self._rs.send_status_query(command)

  async def _detail(self, command, fallback, reports):
    """Keep known objects: retry a lost answer once while connected.
    Report a placeholder on refusal or two lost answers; reread next connect.
    Placeholder partitions/groups mean none, not unknown.
    """
    for attempt in range(2):
      try:
        return await self._rs.send_result_command(command)
      except OperationError as error:
        if isinstance(error, CommunicationError):
          if not self._rs.connected:
            raise
          if attempt == 0:
            continue
          report = CommunicationError(
              f'No answer to {command}; using a placeholder')
        else:
          report = OperationError(
              f'The panel refused {command}; using a placeholder')
        report.__cause__ = error
        reports.append(report)
        return fallback

  def _system_status(self, status, seq=None):
    if self._is_older_than_snapshot('system', seq):
      return
    self._system.update_status(status)
    RiscoLocal._call_handlers(self._system_handlers, copy.copy(self._system))

  def _zone_status(self, zone_id, status, seq=None):
    z = self._zones.get(zone_id)
    if z is None:
      self._report_unknown('zone', zone_id)
      return
    if self._is_older_than_snapshot(('zone', zone_id), seq):
      return
    z.update_status(status)
    RiscoLocal._call_handlers(self._zone_handlers, zone_id, copy.copy(z))

  def _partition_status(self, partition_id, status, seq=None):
    p = self._partitions.get(partition_id)
    if p is None:
      self._report_unknown('partition', partition_id)
      return
    if self._is_older_than_snapshot(('partition', partition_id), seq):
      return
    p.update_status(status)
    RiscoLocal._call_handlers(self._partition_handlers, partition_id, copy.copy(p))

  def _is_older_than_snapshot(self, key, seq):
    """Prevent queued pushes from overwriting newer connect-time snapshots."""
    snapshot = self._snapshots.get(key)
    return seq is not None and snapshot is not None and seq < snapshot

  def _report_unknown(self, kind, object_id):
    # Once per id per connection: the panel repeats a status on every change.
    if (kind, object_id) in self._reported_unknown:
      return
    self._reported_unknown.add((kind, object_id))
    self._error(OperationError(f'Status update for unknown {kind}: {object_id}'))

  def _default(self, command, result, *params):
    RiscoLocal._call_handlers(self._default_handlers, command, result, *params)

  def _event(self, event):
    RiscoLocal._call_handlers(self._event_handlers, event)

  def _error(self, error):
    if not self._error_handlers:
      # Retain errors, including loss during setup, for the first handler.
      self._undelivered.append(error)
      return
    RiscoLocal._call_handlers(self._error_handlers, error)

  def _call_handlers(handlers, *params):
    if len(handlers) > 0:
      async def _gather():
        results = await asyncio.gather(*[h(*params) for h in handlers], return_exceptions=True)
        for result in results:
          if isinstance(result, Exception):
            _LOGGER.error('Error in a Risco handler', exc_info=result)
      task = asyncio.create_task(_gather())
      _handler_tasks.add(task)
      task.add_done_callback(_handler_done)

  async def _listen(self, queue):
    while True:
      try:
        item = await queue.get()
        if isinstance(item, READ_FAILURES):
          # Normalize terminal reads; prefer our close reason to the ensuing EOF.
          reason = self._rs.close_reason or _name(item)
          lost = ConnectionLostError(f'Connection lost: {reason}')
          lost.__cause__ = item
          self._error(lost)
          await self.disconnect()
          break
        if isinstance(item, Exception):
          self._error(item)
          continue

        if item.startswith('CLOCK'):
          # safe to ignore these
          continue

        if item.startswith("EVENT="):
          self._event(item[6:])
          continue

        command, result, *params = item.split("=")
        seq = getattr(item, 'seq', None)

        if command.startswith('ZSTT'):
          self._zone_status(int(command[4:]), result, seq)
        elif command.startswith('PSTT'):
          self._partition_status(int(command[4:]), result, seq)
        elif command.startswith('SSTT'):
          self._system_status(result, seq)
        else:
          self._default(command, result, *params)
      except Exception as error:
        self._error(error)


def _handler_done(task):
  _handler_tasks.discard(task)
  if not task.cancelled() and task.exception() is not None:
    # Report handlers that fail before they can be awaited.
    _LOGGER.error('Error in a Risco handler', exc_info=task.exception())


def _name(error):
  """An exception's type, with its message if it has one."""
  return f'{type(error).__name__}: {error}' if str(error) else type(error).__name__
