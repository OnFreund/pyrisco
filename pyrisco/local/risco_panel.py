import asyncio
from .const import PANEL_TYPE, PANEL_MODEL, PANEL_FW, MAX_ZONES, MAX_PARTS, MAX_OUTPUTS
from .panels import panel_capabilities
from .risco_socket import RiscoSocket
from pyrisco.risco import OperationError, GROUP_ID_TO_NAME

class Partition:
  """A representation of a Risco partition."""

  def __init__(self, partition_id, status):
      """Read partition from response."""
      self._id = partition_id
      self._status = status

  @property
  def id(self):
      """Partition ID number."""
      return self._id

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

  # @property
  # def arming(self):
  #     """Is the partition arming."""
  #     return self.exit_timeout > 0

  @property
  def groups(self):
      """Group arming status."""
      return {GROUP_ID_TO_NAME[g]: (str(g+1) in self._status) for g in range(0,4)}


class Zone:
  def __init__(self, zone_id, status, zone_type, label, partitions, groups, tech):
    self._id = zone_id
    self._status = status
    self._type = int(zone_type)
    self._label = label.strip()
    self._partitions = partitions
    self._groups = groups
    self._tech = tech
  @property
  def id(self):
      """Zone ID number."""
      return self._id

  @property
  def name(self):
      """Zone name."""
      return self._label

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


class RiscoPanel:
  def __init__(self, options):
    self._rs = RiscoSocket(options)
    self._panel_capabilities = None

  async def connect(self):
    await self._rs.connect()
    panel_type = await self._rs.send_result_command("PNLCNF")
    firmware = await self._rs.send_result_command("FSVER?")
    self._panel_capabilities = panel_capabilities(panel_type, firmware)

  async def disconnect(self):
    await self._rs.disconnect()

  async def partitions(self):
    return await self._get_objects(1, self._panel_capabilities[MAX_PARTS], self._create_partition)

  async def zones(self):
    return await self._get_objects(1, self._panel_capabilities[MAX_ZONES], self._create_zone)
    # # get 5 zones at a time
    # n = 5
    # split_zone_ids = [zone_ids[i:i + n] for i in range(0, len(zone_ids), n)]
    # split_zones = [await asyncio.gather(*[self._create_zone(i) for i in ids]) for ids in split_zone_ids]
    # return { z.id: z for zs in split_zones for z in zs if z }

  async def _get_objects(self, min, max, func):
    ids = range(min, min+max)
    temp = await asyncio.gather(*[func(i) for i in ids])
    return { o.id: o for o in temp if o }

  async def _create_partition(self, partition_id):
    try:
      label = await self._rs.send_result_command(f'PLBL{partition_id}?')
      status = await self._rs.send_result_command(f'PSTT{partition_id}?')
    except OperationError:
      return None
    if not 'E' in status:
      return None
    return Partition(partition_id, status)

  async def _create_zone(self, zone_id):
    try:
      status = await self._rs.send_result_command(f'ZSTT*{zone_id}?')
      tech = await self._rs.send_result_command(f'ZLNKTYP{zone_id}?')
    except OperationError:
      return None
    if status.endswith('N') or tech.strip() == 'N':
      return None
    zone_type = await self._rs.send_result_command(f'ZTYPE*{zone_id}?')
    label = await self._rs.send_result_command(f'ZLBL*{zone_id}?')
    partitions = await self._rs.send_result_command(f'ZPART&*{zone_id}?')
    groups = await self._rs.send_result_command(f'ZAREA&*{zone_id}?')
    return Zone(zone_id, status, zone_type, label, partitions, groups, tech)
