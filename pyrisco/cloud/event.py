from pyrisco.common import GROUP_ID_TO_NAME

EVENT_IDS_TO_TYPES = {
  3: "triggered",
  9: "zone bypassed",
  10: "zone unbypassed",
  13: "armed",
  16: "disarmed",
  28: "power lost",
  29: "power restored",
  34: "media lost",
  35: "media restore",
  36: "service needed",
  118: "group arm",
  119: "group arm",
  120: "group arm",
  121: "group arm",
}

class Event:
  """A representation of a Risco event."""

  def __init__(self, raw):
    """Read event from response."""
    self._raw = raw

  @property
  def raw(self):
    return self._raw

  @property
  def type_id(self):
    return self.raw["eventId"]

  @property
  def type_name(self):
    return EVENT_IDS_TO_TYPES.get(self.type_id, "unknown"),

  @property
  def partition_id(self):
    partition_id = self.raw["partAssociationCSV"]
    if partition_id is None:
      return None

    return int(partition_id)

  @property
  def time(self):
    """Time the event was fired."""
    return self.raw["logTime"]

  @property
  def text(self):
    """Event text."""
    return self.raw["eventText"]

  @property
  def name(self):
    """Event name."""
    return self.raw["eventName"]

  @property
  def category_id(self):
    """Event group number."""
    return self.raw["group"]

  @property
  def category_name(self):
    """Event group number."""
    return self.raw["groupName"]

  @property
  def zone_id(self):
    if self.raw["sourceType"] == 1:
      return self.raw["sourceID"] - 1
    return None

  @property
  def user_id(self):
    if self.raw["sourceType"] == 2:
      return self.raw["sourceID"]
    return None

  @property
  def group(self):
    if self.type_id in range(118, 122):
      return GROUP_ID_TO_NAME[self.type_id - 118]
    return None

  @property
  def priority(self):
    return self.raw["priority"]

  @property
  def source_id(self):
    return self._source_id
