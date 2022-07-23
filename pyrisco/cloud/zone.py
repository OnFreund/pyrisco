from pyrisco.common import GROUP_ID_TO_NAME, Zone as BaseZone

class Zone(BaseZone):
  """A representation of a Risco zone."""

  def __init__(self, api, raw):
    """Read zone from response."""
    self._api = api
    self._raw = raw

  async def bypass(self, bypass):
    return await self._api.bypass_zone(self.id, bypass)

  @property
  def id(self):
    """Zone ID number."""
    return self._raw["zoneID"]

  @property
  def name(self):
    """Zone name."""
    return self._raw["zoneName"]

  @property
  def type(self):
    """Zone type."""
    return self._raw["zoneType"]

  @property
  def triggered(self):
    """Is the zone triggered."""
    return self._raw["status"] == 1

  @property
  def bypassed(self):
    """Is the zone bypassed."""
    return self._raw["status"] == 2
