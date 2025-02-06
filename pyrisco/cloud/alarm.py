from .partition import Partition
from .zone import Zone

class Alarm:
  """A representation of a Risco alarm system."""

  def __init__(self, api, raw, assumed_control_panel_state):
    """Read alarm from response."""
    self._api = api
    self._raw = raw
    self._partitions = None
    self._zones = None
    self._assumed_control_panel_state = assumed_control_panel_state

  @property
  def assumed_control_panel_state(self):
    """Return True if the state is based on RiscoCloud instead of reading it from the control panel."""
    return self._assumed_control_panel_state

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
