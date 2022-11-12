from pyrisco.common import GROUP_ID_TO_NAME, Zone as BaseZone

class Zone(BaseZone):
  def __init__(self, panel, zone_id, status, zone_type, label, partitions, groups, tech):
    self._panel = panel
    self._id = zone_id
    self._status = status
    self._type = zone_type
    self._name = label.strip()
    self._partitions = partitions
    self._groups = int(groups, 16)
    self._tech = tech

  async def bypass(self, bypass):
    """Bypass or unbypass the zone."""
    return await self._panel.bypass_zone(self.id, bypass)

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
  def alarmed(self):
      """Is the zone causing an alarm."""
      return 'a' in self._status

  @property
  def armed(self):
      """Is the zone armed."""
      return 'A' in self._status

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

  def update_status(self, status):
    self._status = status
