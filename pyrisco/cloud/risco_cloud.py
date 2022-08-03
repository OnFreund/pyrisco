"""Implementation of a Risco Cloud connection."""

import aiohttp
import asyncio

from .alarm import Alarm
from .event import Event
from pyrisco.common import UnauthorizedError, CannotConnectError, OperationError, GROUP_ID_TO_NAME


LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"
SITE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/GetAll"
PIN_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/Login"
STATE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetState"
CONTROL_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/PartArm"
EVENTS_URL = (
  "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetEventLog"
)
BYPASS_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/SetZoneBypassStatus"

NUM_RETRIES = 3


class RiscoCloud:
  """A connection to a Risco alarm system."""

  def __init__(self, username, password, pin, language="en"):
    """Initialize the object."""
    self._username = username
    self._password = password
    self._pin = pin
    self._language = language
    self._access_token = None
    self._session_id = None
    self._site_id = None
    self._site_name = None
    self._site_uuid = None
    self._session = None
    self._created_session = False

  async def _authenticated_post(self, url, body):
    headers = {
      "Content-Type": "application/json",
      "authorization": "Bearer " + self._access_token,
    }
    async with self._session.post(url, headers=headers, json=body) as resp:
      json = await resp.json()

    if json["status"] == 401:
      raise UnauthorizedError(json["errorText"])

    if "result" in json and json["result"] != 0:
      raise OperationError(str(json))

    return json["response"]

  async def _site_post(self, url, body):
    site_url = url % self._site_id
    for i in range(NUM_RETRIES):
      try:
        site_body = {
            **body,
            "fromControlPanel": True,
            "sessionToken": self._session_id,
        }
        return await self._authenticated_post(site_url, site_body)
      except UnauthorizedError:
        if i + 1 == NUM_RETRIES:
          raise
        await self.close()
        await self.login()

  async def _login_user_pass(self):
    headers = {"Content-Type": "application/json"}
    body = {"userName": self._username, "password": self._password}
    try:
      async with self._session.post(
        LOGIN_URL, headers=headers, json=body
      ) as resp:
        json = await resp.json()
        if json["status"] == 401:
          raise UnauthorizedError("Invalid username or password")
        self._access_token = json["response"].get("accessToken")
    except aiohttp.client_exceptions.ClientConnectorError as e:
      raise CannotConnectError from e

    if not self._access_token:
      raise UnauthorizedError("Invalid username or password")

  async def _login_site(self):
    resp = await self._authenticated_post(SITE_URL, {})
    self._site_id = resp[0]["id"]
    self._site_name = resp[0]["name"]
    self._site_uuid = resp[0]["siteUUID"]

  async def _login_session(self):
    body = {"languageId": self._language, "pinCode": self._pin}
    url = PIN_URL % self._site_id
    resp = await self._authenticated_post(url, body)
    self._session_id = resp["sessionId"]

  async def _init_session(self, session):
    await self.close()
    if self._session is None:
      if session is None:
        self._session = aiohttp.ClientSession()
        self._created_session = True
      else:
        self._session = session

  async def _send_control_command(self, body):
    resp = await self._site_post(CONTROL_URL, body)
    return Alarm(self, resp)

  async def close(self):
    """Close the connection."""
    self._session_id = None
    if self._created_session == True and self._session is not None:
      await self._session.close()
      self._session = None
      self._created_session = False

  async def login(self, session=None):
    """Login to Risco Cloud."""
    if self._session_id:
        return

    await self._init_session(session)
    await self._login_user_pass()
    await self._login_site()
    await self._login_session()

  async def get_state(self):
    """Get partitions and zones."""
    resp = await self._site_post(STATE_URL, {})
    return Alarm(self, resp["state"]["status"])

  async def disarm(self, partition):
    """Disarm the alarm."""
    body = {
      "partitions": [{"id": partition, "armedState": 1}],
    }
    return await self._send_control_command(body)

  async def arm(self, partition):
    """Arm the alarm."""
    body = {
      "partitions": [{"id": partition, "armedState": 3}],
    }
    return await self._send_control_command(body)

  async def partial_arm(self, partition):
    """Partially-arm the alarm."""
    body = {
      "partitions": [{"id": partition, "armedState": 2}],
    }
    return await self._send_control_command(body)

  async def group_arm(self, partition, group):
    """Arm a specific group."""
    if isinstance(group, str):
      group = GROUP_ID_TO_NAME.index(group)

    body = {
      "partitions": [{"id": partition, "groups": [{"id": group, "state": 3}]}],
    }
    return await self._send_control_command(body)

  async def get_events(self, newer_than, count=10):
    """Get event log."""
    body = {
      "count": count,
      "newerThan": newer_than,
      "offset": 0,
    }
    response = await self._site_post(EVENTS_URL, body)
    return [Event(e) for e in response["controlPanelEventsList"]]

  async def bypass_zone(self, zone, bypass):
    """Bypass or unbypass a zone."""
    status = 2 if bypass else 3
    body = {"zones": [{"trouble": 0, "ZoneID": zone, "Status": status}]}
    resp = await self._site_post(BYPASS_URL, body)
    return Alarm(self, resp)

  @property
  def site_id(self):
    """Site ID of the Alarm instance."""
    return self._site_id

  @property
  def site_name(self):
    """Site name of the Alarm instance."""
    return self._site_name

  @property
  def site_uuid(self):
    """Site UUID of the Alarm instance."""
    return self._site_uuid
