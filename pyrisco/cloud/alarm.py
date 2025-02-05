from .partition import Partition
from .zone import Zone

class Alarm:
  """A representation of a Risco alarm system."""

  def __init__(self, api, raw, from_control_panel):
    """Read alarm from response."""
    self._api = api
    self._raw = raw
    self._partitions = None
    self._zones = None
    self._from_control_panel = from_control_panel

  @property
  def is_from_control_panel(self):
    """Is information coming from Control Panel via RiscoCloud."""
    return self._from_control_panel

  @property
  def partitions(self):
    """Alarm partitions."""
    if self._partitions is None:
      self._partitions = {p["id"]: Partition(self._api, p) for p in self._raw["partitions"]}
    return self._partitions

  @property
  def zones(self):
    """Alarm zones."""
    if self._zones is None:
      self._zones = {z["zoneID"]: Zone(self._api, z) for z in self._raw["zones"]}
    return self._zones
