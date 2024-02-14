import asyncio
import copy
from .const import PANEL_TYPE, PANEL_MODEL, PANEL_FW, MAX_ZONES, MAX_PARTS, MAX_OUTPUTS
from .panels import panel_capabilities
from .partition import Partition
from .zone import Zone
from .risco_socket import RiscoSocket
from pyrisco.common import OperationError, GROUP_ID_TO_NAME


class RiscoLocal:
  def __init__(self, host, port, code, **kwargs):
    self._rs = RiscoSocket(host, port, code, **kwargs)
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
    if panel_type.startswith("RP"):
      firmware = await self._rs.send_result_command("FSVER?")
    else:
      firmware = ""
    self._panel_capabilities = panel_capabilities(panel_type, firmware)
    self._id = await self._rs.send_result_command("PNLSERD")
    self._zones = await self._init_zones()
    self._partitions = await self._init_partitions()
    self._listen_task = asyncio.create_task(self._listen(self._rs.queue))

  async def disconnect(self):
    await self._rs.disconnect()
    self._listen_task.cancel()
    self._listen_task = None

  def add_error_handler(self, handler):
    return RiscoLocal._add_handler(self._error_handlers, handler)

  def add_event_handler(self, handler):
    return RiscoLocal._add_handler(self._event_handlers, handler)

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

  def _add_handler(handlers, handler):
    handlers.append(handler)
    def _remove():
      handlers.remove(handler)
    return _remove

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
    return Partition(self, partition_id, label, status)

  async def _create_zone(self, zone_id):
    try:
      zone_type = int(await self._rs.send_result_command(f'ZTYPE*{zone_id}?'))
      if zone_type == 0:
        return None

      tech = await self._rs.send_result_command(f'ZLNKTYP{zone_id}?')
      if tech.strip() == 'N':
        return None

      status = await self._rs.send_result_command(f'ZSTT*{zone_id}?')
      if status.endswith('N'):
        return None

      label = await self._rs.send_result_command(f'ZLBL*{zone_id}?')
      partitions = await self._rs.send_result_command(f'ZPART&*{zone_id}?')
      groups = await self._rs.send_result_command(f'ZAREA&*{zone_id}?')
      return Zone(self, zone_id, status, zone_type, label, partitions, groups, tech)
    except OperationError:
      return None

  def _zone_status(self, zone_id, status):
    z = self._zones[zone_id]
    z.update_status(status)
    RiscoLocal._call_handlers(self._zone_handlers, zone_id, copy.copy(z))

  def _partition_status(self, partition_id, status):
    p = self._partitions[partition_id]
    p.update_status(status)
    RiscoLocal._call_handlers(self._partition_handlers, partition_id, copy.copy(p))

  def _default(self, command, result, *params):
    RiscoLocal._call_handlers(self._default_handlers, command, result, *params)

  def _event(self, event):
    RiscoLocal._call_handlers(self._event_handlers, event)

  def _error(self, error):
    RiscoLocal._call_handlers(self._error_handlers, error)

  def _call_handlers(handlers, *params):
    if len(handlers) > 0:
      async def _gather():
        await asyncio.gather(*[h(*params) for h in handlers])
      asyncio.create_task(_gather())

  async def _listen(self, queue):
    while True:
      try:
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
      except Exception as error:
        self._error(error)
