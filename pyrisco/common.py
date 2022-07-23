GROUP_ID_TO_NAME = ["A", "B", "C", "D"]

class Partition:
  """A representation of a Risco partition."""

  async def disarm(self):
    """Disarm the partition."""
    raise NotImplementedError

  async def arm(self):
    """Arm the partition."""
    raise NotImplementedError

  async def partial_arm(self):
    """Partially-arm the partition."""
    raise NotImplementedError

  async def group_arm(self, group):
    """Arm a group on the partition."""
    raise NotImplementedError

  @property
  def id(self):
    """Partition ID number."""
    raise NotImplementedError

  @property
  def disarmed(self):
    """Is the partition disarmed."""
    raise NotImplementedError

  @property
  def partially_armed(self):
    """Is the partition partially-armed."""
    raise NotImplementedError

  @property
  def armed(self):
    """Is the partition armed."""
    raise NotImplementedError

  @property
  def triggered(self):
    """Is the partition triggered."""
    raise NotImplementedError

  @property
  def exit_timeout(self):
    """Time remaining till armed."""
    raise NotImplementedError

  @property
  def arming(self):
    """Is the partition arming."""
    raise NotImplementedError

  @property
  def groups(self):
    """Group arming status."""
    raise NotImplementedError

class Zone:
  """A representation of a Risco zone."""

  async def bypass(self, bypass):
    """Bypass or unbypass the zone."""
    raise NotImplementedError

  @property
  def id(self):
    raise NotImplementedError

  @property
  def name(self):
    raise NotImplementedError

  @property
  def type(self):
    raise NotImplementedError

  @property
  def triggered(self):
    raise NotImplementedError

  @property
  def bypassed(self):
    raise NotImplementedError

class UnauthorizedError(Exception):
  """Exception to indicate an error in authorization."""


class CannotConnectError(Exception):
  """Exception to indicate an error in authorization."""


class OperationError(Exception):
  """Exception to indicate an error in operation."""
