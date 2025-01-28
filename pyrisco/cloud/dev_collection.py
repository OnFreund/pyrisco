class Device:
  """A representation of a Risco Device."""

  def __init__(self, raw):
    """Read event from response."""
    self._raw = raw

  @property
  def raw(self):
    return self._raw

  @property
  def num(self):
    return self.raw["num"]

  @property
  def description(self):
    return self.raw["description"]

  @property
  def extra(self):
    return self.raw["extra"]


class DevCollection:
  """A representation of a Risco Device Collection."""

  def __init__(self, raw):
    """Read event from response."""
    self._raw = raw
    self._devices = None

  @property
  def raw(self):
    return self._raw

  @property
  def device_type(self):
    return self.raw["devType"]

  @property
  def devices(self):
    """Alarm dev collections."""
    if self._devices is None:
      self._devices = {d["num"]: Device(d) for d in self._raw.get("devList", [])}
    return self._devices
