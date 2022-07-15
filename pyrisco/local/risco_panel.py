import asyncio
from .const import PANEL_TYPE, PANEL_MODEL, PANEL_FW, MAX_ZONES, MAX_PARTS, MAX_OUTPUTS
from .panels import panel_capabilities
from .risco_socket import RiscoSocket
from pyrisco.risco import OperationError, GROUP_ID_TO_NAME

class Partition:
  """A representation of a Risco partition."""

  def __init__(self, partition_id, label, status):
      """Read partition from response."""
      self._id = partition_id
      self._status = status
      self._name = label.strip()

  def update_status(self, status):
    self._status = status

  @property
  def id(self):
      """Partition ID number."""
      return self._id

  @property
  def name(self):
      """Partition name."""
      return self._name

  @property
  def disarmed(self):
      """Is the partition disarmed."""
      return not (self.armed or self.partially_armed)

  @property
  def partially_armed(self):
      """Is the partition partially-armed."""
      return 'H' in self._status

  @property
  def armed(self):
      """Is the partition armed."""
      return 'A' in self._status

  @property
  def triggered(self):
      """Is the partition triggered."""
      return 'a' in self._status

  @property
  def ready(self):
      """Is the partition ready."""
      return 'R' in self._status

  @property
  def arming(self):
      """Is the partition arming."""
      return self.disarmed and not self.ready

  @property
  def groups(self):
      """Group arming status."""
      return {GROUP_ID_TO_NAME[g]: (str(g+1) in self._status) for g in range(0,4)}


class Zone:
  def __init__(self, zone_id, status, zone_type, label, partitions, groups, tech):
    self._id = zone_id
    self._status = status
    self._type = int(zone_type)
    self._name = label.strip()
    self._partitions = partitions
    self._groups = int(groups, 16)
    self._tech = tech

  def update_status(self, status):
    self._status = status

  @property
  def id(self):
      """Zone ID number."""
      return self._id

  @property
  def name(self):
      """Zone name."""
      return self._name

  @property
  def type(self):
      """Zone type."""
      return self._type

  @property
  def triggered(self):
      """Is the zone triggered."""
      return 'O' in self._status

  @property
  def bypassed(self):
      """Is the zone bypassed."""
      return 'Y' in self._status

  @property
  def groups(self):
      """Groups the zone belongs to."""
      return [GROUP_ID_TO_NAME[i] for i in range(0,4) if ((2**i) & self._groups) > 0]

  @property
  def partitions(self):
      """partitions the zone belongs to."""
      ps = zip([int(p, 16) for p in self._partitions], range(0, len(self._partitions)))
      return [i*4 + p + 1 for c, i in ps for p in range(0,4) if ((2**p) & c) > 0]


class RiscoPanel:
  def __init__(self, options):
    self._rs = RiscoSocket(options)
    self._panel_capabilities = None
    self._listen_task = None
    self._zone_handlers = []
    self._partition_handlers = []
    self._error_handlers = []
    self._default_handlers = []
    self._event_handlers = []
    self._zones = None
    self._partitions = None
    self._id = None

  async def connect(self):
    await self._rs.connect()
    panel_type = await self._rs.send_result_command("PNLCNF")
    firmware = await self._rs.send_result_command("FSVER?")
    self._panel_capabilities = panel_capabilities(panel_type, firmware)
    self._id = int(await self._rs.send_result_command("PNLSERD"))
    self._zones = await self._init_zones()
    self._partitions = await self._init_partitions()
    self._listen_task = asyncio.create_task(self._listen(self._rs.queue))

  async def disconnect(self):
    await self._rs.disconnect()
    self._listen_task.cancel()
    self._listen_task = None

  def add_error_handler(self, handler):
    return RiscoPanel._add_handler(self._error_handlers, handler)

  def add_event_handler(self, handler):
    return RiscoPanel._add_handler(self._event_handlers, handler)

  def add_zone_handler(self, handler):
    return RiscoPanel._add_handler(self._zone_handlers, handler)

  def add_zone_handler(self, handler):
    return RiscoPanel._add_handler(self._partition_handlers, handler)

  def add_default_handler(self, handler):
    return RiscoPanel._add_handler(self._default_handlers, handler)

  @property
  def id(self):
    return self._id

  @property
  def zones(self):
    return self._zones

  @property
  def partitions(self):
    return self._partitions

  async def disarm(self, partition_id):
    """Disarm the alarm."""
    return await self._rs.send_ack_command(f'DISARM={partition_id}')

  async def arm(self, partition_id):
    """Arm the alarm."""
    return await self._rs.send_ack_command(f'ARM={partition_id}')

  async def partial_arm(self, partition_id):
      """Partially-arm the alarm."""
      return await self._rs.send_ack_command(f'STAY={partition_id}')

  async def group_arm(self, partition_id, group):
    """Arm a specific group."""
    if isinstance(group, str):
        group = GROUP_ID_TO_NAME.index(group) + 1

    return await self._rs.send_ack_command(f'GARM*{group}={partition_id}')

  # async def get_events(self, newer_than, count=10):
  #   """Get event log."""
  #   await rp._rs.send_ack_command("TLOG=15/07/2022 18:00")
  #   await rp._rs.send_ack_command("QLOG=10")
  #   body = {
  #       "count": count,
  #       "newerThan": newer_than,
  #       "offset": 0,
  #   }
  #   response = await self._site_post(EVENTS_URL, body)
  #   return [Event(e) for e in response["controlPanelEventsList"]]

  async def bypass_zone(self, zone_id, bypass):
    """Bypass or unbypass a zone."""
    if self.zones[zone_id].bypassed != bypass:
      await self._rs.send_ack_command(F'ZBYPAS={zone_id}')

  def _add_handler(handlers, handler):
    handlers.add(handler)
    def _remove():
      handlers.remove(handler)
    return remove

  async def _init_partitions(self):
    return await self._get_objects(1, self._panel_capabilities[MAX_PARTS], self._create_partition)

  async def _init_zones(self):
    return await self._get_objects(1, self._panel_capabilities[MAX_ZONES], self._create_zone)

  async def _get_objects(self, min, max, func):
    ids = range(min, min+max)
    temp = await asyncio.gather(*[func(i) for i in ids])
    return { o.id: o for o in temp if o }

  async def _create_partition(self, partition_id):
    try:
      status = await self._rs.send_result_command(f'PSTT{partition_id}?')
      if not 'E' in status:
        return None

      label = await self._rs.send_result_command(f'PLBL{partition_id}?')
    except OperationError:
      return None
    return Partition(partition_id, label, status)

  async def _create_zone(self, zone_id):
    try:
      status = await self._rs.send_result_command(f'ZSTT*{zone_id}?')
      if status.endswith('N'):
        return None

      tech = await self._rs.send_result_command(f'ZLNKTYP{zone_id}?')
      if tech.strip() == 'N':
        return None

      zone_type = await self._rs.send_result_command(f'ZTYPE*{zone_id}?')
      label = await self._rs.send_result_command(f'ZLBL*{zone_id}?')
      partitions = await self._rs.send_result_command(f'ZPART&*{zone_id}?')
      groups = await self._rs.send_result_command(f'ZAREA&*{zone_id}?')
      return Zone(zone_id, status, zone_type, label, partitions, groups, tech)
    except OperationError:
      return None

  def _zone_status(self, zone_id, status):
    print(f'Zone update {zone_id}: {status}')
    z = self._zones[zone_id]
    z.update_status(status)
    RiscoPanel._call_handlers(self._zone_handlers, zone_id, z)

  def _partition_status(self, partition_id, status):
    print(f'Partition update {partition_id}: {status}')
    p = self._partitions[partition_id]
    p.update_status(status)
    RiscoPanel._call_handlers(self._partition_handlers, partition_id, p)

  def _default(self, command, result, *params):
    print(f'Default: {command}, {result}, {params}')
    RiscoPanel._call_handlers(self._default_handlers, command, result, *params)

  def _event(self, event):
    print(f'Event: {event}')
    RiscoPanel._call_handlers(self._event_handlers, event)

  def _error(self, error):
    print(f'Error: {error}')
    RiscoPanel._call_handlers(self._error_handlers, error)

  def _call_handlers(handlers, *params):
    if len(handlers) > 0:
      asyncio.create_task(asyncio.gather(*[h(*params) for h in handlers]))

  async def _listen(self, queue):
    while True:
      item = await queue.get()
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

      if command.startswith('ZSTT'):
        self._zone_status(int(command[4:]), result)
      elif command.startswith('PSTT'):
        self._partition_status(int(command[4:]), result)
      else:
        self._default(command, result, *params)
