USER_TYPES = {
  9: "disabled",
  12: "master",
  13: "user",
}


class User:
  """A representation of a Risco user."""

  def __init__(self, raw):
    """Read user from response."""
    self._raw = raw

  @property
  def raw(self):
    return self._raw

  @property
  def user_id(self):
    return self.raw["userID"]

  @property
  def name(self):
    return self.raw["userName"]

  @property
  def type_id(self):
    return self.raw["userType"]

  @property
  def type_name(self):
    return USER_TYPES.get(self.type_id, "unknown"),

  @property
  def part(self):
    """User part."""
    return self.raw["part"]
