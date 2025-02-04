from .partition import Partition
from .zone import Zone
from .user import User
from .dev_collection import DevCollection


class Alarm:
  """A representation of a Risco alarm system."""

  def __init__(self, api, raw, from_control_panel):
    """Read alarm from response."""
    self._api = api
    self._raw = raw
    self._raw_status = raw["status"]
    self._partitions = None
    self._zones = None
    self._users = None
    self._dev_collections = None
    self._from_control_panel = from_control_panel

  @property
  def is_from_control_panel(self):
    """Is information comes from control Panel via RiscoCloud."""
    return self._from_control_panel

  @property
  def is_online(self):
    """Is the alarm online."""
    return self._raw["isOnline"]

  @property
  def last_connected_time(self):
    """Last connected time."""
    return self._raw["lastConnectedTime"]

  @property
  def last_log_update(self):
    """Last log update."""
    return self._raw["lastLogUpdate"]

  @property
  def last_status_update(self):
    """Last status update."""
    return self._raw["lastStatusUpdate"]

  @property
  def last_event_reported(self):
    """Last event reported."""
    return self._raw["lastEvReported"]

  @property
  def cp_time(self):
    """CP time."""
    return self._raw_status["cpTime"]

  @property
  def system_status(self):
    """System status."""
    return self._raw_status["systemStatus"]

  @property
  def system_ready(self):
    """System ready."""
    return self._raw_status["systemReady"]

  @property
  def trouble(self):
    """Trouble."""
    return self._raw_status["trouble"]

  @property
  def bell_status(self):
    """Bell status."""
    return self._raw_status["bellStatus"]

  @property
  def bell_on(self):
    """Bell on."""
    return self._raw_status["bellOn"]

  @property
  def alarm_pending(self):
    """Alarm pending."""
    return self._raw_status["alarmPending"]

  @property
  def battery_low(self):
    """Battery low."""
    return self._raw_status["batteryLow"]

  @property
  def ac_lost(self):
    """AC lost."""
    return self._raw_status["acLost"]

  @property
  def partitions(self):
    """Alarm partitions."""
    if self._partitions is None:
      self._partitions = {p["id"]: Partition(self._api, p) for p in self._raw_status.get("partitions", [])}
    return self._partitions

  @property
  def dev_collections(self):
    """Alarm dev collections."""
    if self._dev_collections is None:
      self._dev_collections = {dc["devType"]: DevCollection(dc) for dc in self._raw_status.get("devCollection", [])}
    return self._dev_collections

  @property
  def zones(self):
    """Alarm zones."""
    if self._zones is None:
      self._zones = {z["zoneID"]: Zone(self._api, z) for z in self._raw_status.get("zones", [])}
    return self._zones

  @property
  def users(self):
    """Alarm users."""
    if self._users is None:
      enabled_users = [u for u in self._raw_status["users"] if u["userType"] != 9]
      self._users = {z["userID"]: User(z) for z in enabled_users}
    return self._users
