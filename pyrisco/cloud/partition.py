from pyrisco.common import GROUP_ID_TO_NAME, Partition as BasePartition

class Partition(BasePartition):
  """A representation of a Risco partition."""

  def __init__(self, api, raw):
    """Read partition from response."""
    self._api = api
    self._raw = raw

  async def disarm(self):
    return await self._api.disarm(self.id)

  async def arm(self):
    return await self._api.arm(self.id)

  async def partial_arm(self):
    return await self._api.partial_arm(self.id)

  async def group_arm(self, group):
    return await self._api.group_arm(self.id, group)

  @property
  def id(self):
    """Partition ID number."""
    return self._raw["id"]

  @property
  def disarmed(self):
    """Is the partition disarmed."""
    return self._raw["armedState"] == 1

  @property
  def partially_armed(self):
    """Is the partition partially-armed."""
    return self._raw["armedState"] == 2

  @property
  def armed(self):
    """Is the partition armed."""
    return self._raw["armedState"] == 3

  @property
  def triggered(self):
    """Is the partition triggered."""
    return self._raw["alarmState"] == 1

  @property
  def exit_timeout(self):
    """Time remaining till armed."""
    return self._raw["exitDelayTO"]

  @property
  def arming(self):
    """Is the partition arming."""
    return self.exit_timeout > 0

  @property
  def groups(self):
    """Group arming status."""
    if self._raw.get("groups") is None:
      return {}
    return {GROUP_ID_TO_NAME[g["id"]]: g["state"] == 3 for g in self._raw["groups"]}
