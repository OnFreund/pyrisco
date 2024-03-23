from pyrisco.common import System as BaseSystem

class System(BaseSystem):
  def __init__(self, panel, label, status):
      """Read system from response."""
      self._panel = panel
      self._status = status
      self._name = label.strip()

  @property
  def name(self):
      """System name."""
      return self._name

  @property
  def low_battery_trouble(self):
      return 'B' in self._status

  @property
  def ac_trouble(self):
      return 'A' in self._status

  @property
  def monitoring_station_1_trouble(self):
      return '1' in self._status

  @property
  def monitoring_station_2_trouble(self):
      return '2' in self._status

  @property
  def monitoring_station_3_trouble(self):
      return '3' in self._status

  @property
  def phone_line_trouble(self):
      return 'P' in self._status

  @property
  def clock_trouble(self):
      return 'C' in self._status

  @property
  def box_tamper(self):
      return 'X' in self._status

  @property
  def programming_mode(self):
      return 'I' in self._status

  def update_status(self, status):
    self._status = status
