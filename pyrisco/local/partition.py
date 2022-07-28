from pyrisco.common import GROUP_ID_TO_NAME, Partition as BasePartition

class Partition(BasePartition):
  def __init__(self, panel, partition_id, label, status):
      """Read partition from response."""
      self._panel = panel
      self._id = partition_id
      self._status = status
      self._name = label.strip()

  async def disarm(self):
    """Disarm the partition."""
    return await self._panel.disarm(self.id)

  async def arm(self):
    """Arm the partition."""
    return await self._panel.arm(self.id)

  async def partial_arm(self):
    """Partially-arm the partition."""
    return await self._panel.partial_arm(self.id)

  async def group_arm(self, group):
    """Arm a group on the partition."""
    return await self._panel.group_arm(self.id, group)

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
      # return self.disarmed and not self.ready
      return False

  @property
  def groups(self):
      """Group arming status."""
      return {GROUP_ID_TO_NAME[g]: (str(g+1) in self._status) for g in range(0,4)}

  def update_status(self, status):
    self._status = status
