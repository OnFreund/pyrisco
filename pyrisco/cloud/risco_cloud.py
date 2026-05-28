"""Implementation of a Risco Cloud connection."""

import aiohttp
import asyncio
import json
from datetime import datetime

from .alarm import Alarm
from .event import Event
from pyrisco.common import UnauthorizedError, CannotConnectError, OperationError, RetryableOperationError, MaxRetriesError, GROUP_ID_TO_NAME


def _parse_timestamp(ts_str):
  """Parse an ISO 8601 timestamp, accepting both 'Z' and '+00:00' UTC suffixes."""
  return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"
SITE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/GetAll"
PIN_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/Login"
STATE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetState"
CONTROL_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/PartArm"
EVENTS_URL = (
  "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetEventLog"
)
BYPASS_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/SetZoneBypassStatus"
SSE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/sse/connect"

NUM_RETRIES = 3
RETRYABLE_RESULT_CODE = 72
RECONNECT_INITIAL_DELAY = 1
RECONNECT_MAX_DELAY = 60
RECONNECT_MAX_ATTEMPTS = 5


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
    self._state_handlers = []
    self._error_handlers = []
    self._event_handlers = []
    self._last_status_update = None
    self._last_event_update = None
    self._latest_state = None
    self._subscription_task = None

  @staticmethod
  def _add_handler(handlers, handler):
    handlers.append(handler)
    def _remove():
      handlers.remove(handler)
    return _remove

  async def _authenticated_post(self, url, body):
    headers = {
      "Content-Type": "application/json",
      "authorization": f"Bearer {self._access_token}",
      "User-Agent": "pyrisco/1.0",
    }
    async with self._session.post(url, headers=headers, json=body) as resp:
      json_resp = await resp.json()

    if json_resp["status"] == 401:
      raise UnauthorizedError(json_resp["errorText"])

    if "result" in json_resp and json_resp["result"] == RETRYABLE_RESULT_CODE:
      raise RetryableOperationError(str(json_resp))

    if "result" in json_resp and json_resp["result"] != 0:
      raise OperationError(str(json_resp))

    return json_resp["response"]

  async def _site_post(self, url, body):
    site_url = url % self._site_id
    from_control_panel = True
    for i in range(NUM_RETRIES):
      try:
        site_body = {
            **body,
            "fromControlPanel": from_control_panel,
            "sessionToken": self._session_id,
        }
        return await self._authenticated_post(site_url, site_body), not from_control_panel
      except (UnauthorizedError, RetryableOperationError) as e:
        if i + 1 == NUM_RETRIES:
          if isinstance(e, RetryableOperationError):
            raise OperationError("Failed to perform operation after retries") from e
          raise
        if isinstance(e, RetryableOperationError):
          from_control_panel = False
        elif isinstance(e, UnauthorizedError):
          await self._relogin()

  async def _login_user_pass(self):
    headers = {"Content-Type": "application/json", "User-Agent": "pyrisco/1.0"}
    body = {"userName": self._username, "password": self._password}
    try:
      async with self._session.post(
        LOGIN_URL, headers=headers, json=body
      ) as resp:
        json_resp = await resp.json()
        if json_resp["status"] == 401:
          raise UnauthorizedError("Invalid username or password")
        self._access_token = json_resp["response"].get("accessToken")
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

  async def _relogin(self):
    """Refresh credentials in-place without closing the session or the SSE task."""
    self._session_id = None
    await self._login_user_pass()
    await self._login_site()
    await self._login_session()

  async def _send_control_command(self, body):
    resp, assumed_control_panel_state = await self._site_post(CONTROL_URL, body)
    return Alarm(self, resp, assumed_control_panel_state)

  async def _sse_loop(self):
    attempt = 0
    while True:
      try:
        url = SSE_URL % self._site_id
        headers = {
          "authorization": f"Bearer {self._access_token}",
          "User-Agent": "pyrisco/1.0",
          "sessionToken": self._session_id,
        }
        params = {"sessionToken": self._session_id}
        async with self._session.get(url, headers=headers, params=params) as resp:
          resp.raise_for_status()
          # SSE connection is now open — fetch initial state before consuming
          # messages so no state changes in between can be missed.
          initial_resp, assumed = await self._site_post(STATE_URL, {})
          alarm = Alarm(self, initial_resp["state"]["status"], assumed)
          self._latest_state = alarm
          for handler in list(self._state_handlers):
            await handler(alarm)
          event_type = None
          data_line = None
          async for line_bytes in resp.content:
            line = line_bytes.decode("utf-8").rstrip("\r\n")
            if line.startswith("event:"):
              event_type = line[6:].strip()
            elif line.startswith("data:"):
              data_line = line[5:].strip()
            elif line == "" and event_type == "runtimeUpdate" and data_line:
              await self._handle_runtime_update(json.loads(data_line))
              event_type = None
              data_line = None
        attempt = 0  # successful connection — reset backoff for next drop
        await asyncio.sleep(RECONNECT_INITIAL_DELAY)  # small delay before reconnecting on clean EOF
      except asyncio.CancelledError:
        raise
      except Exception as e:
        attempt += 1
        if attempt >= RECONNECT_MAX_ATTEMPTS:
          for handler in list(self._error_handlers):
            await handler(MaxRetriesError(e))
          return
        for handler in list(self._error_handlers):
          await handler(e)
        delay = min(RECONNECT_INITIAL_DELAY * (2 ** (attempt - 1)), RECONNECT_MAX_DELAY)
        await asyncio.sleep(delay)

  async def _handle_runtime_update(self, data):
    if data.get("IsOffline"):
      return
    ts_str = data.get("LastStatusUpdate")
    if ts_str:
      update_time = _parse_timestamp(ts_str)
      if self._last_status_update is None or update_time > self._last_status_update:
        resp, assumed = await self._site_post(STATE_URL, {})
        alarm = Alarm(self, resp["state"]["status"], assumed)
        self._last_status_update = update_time
        self._latest_state = alarm
        for handler in list(self._state_handlers):
          await handler(alarm)
    event_ts_str = data.get("LastEventUpdated")
    if event_ts_str and self._event_handlers:
      event_time = _parse_timestamp(event_ts_str)
      last_event_time = _parse_timestamp(self._last_event_update) if self._last_event_update else None
      if last_event_time is None or event_time > last_event_time:
        events = await self.get_events(self._last_event_update)
        self._last_event_update = event_ts_str
        for handler in list(self._event_handlers):
          await handler(events)

  async def close(self):
    """Close the connection."""
    self._session_id = None
    if self._subscription_task:
      self._subscription_task.cancel()
      try:
        await self._subscription_task
      except (asyncio.CancelledError, Exception):
        pass
      self._subscription_task = None
    if self._created_session and self._session is not None:
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

  def add_state_handler(self, handler):
    """Register an async callback for state updates. Returns a remover callable."""
    return RiscoCloud._add_handler(self._state_handlers, handler)

  def add_error_handler(self, handler):
    """Register an async callback for SSE errors. Returns a remover callable."""
    return RiscoCloud._add_handler(self._error_handlers, handler)

  def add_event_handler(self, handler):
    """Register an async callback for event log updates via SSE. Returns a remover callable."""
    return RiscoCloud._add_handler(self._event_handlers, handler)

  async def subscribe_states(self):
    """Start listening for push state updates via SSE."""
    self._subscription_task = asyncio.create_task(self._sse_loop())

  async def get_state(self):
    """Get partitions and zones."""
    if self._latest_state is not None:
      return self._latest_state
    resp, assumed_control_panel_state = await self._site_post(STATE_URL, {})
    return Alarm(self, resp["state"]["status"], assumed_control_panel_state)

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
    response, assumed_control_panel_state = await self._site_post(EVENTS_URL, body)
    if response is None:
      return []
    return [Event(e) for e in response["controlPanelEventsList"]]

  async def bypass_zone(self, zone, bypass):
    """Bypass or unbypass a zone."""
    status = 2 if bypass else 3
    body = {"zones": [{"trouble": 0, "ZoneID": zone, "Status": status}]}
    resp, assumed_control_panel_state = await self._site_post(BYPASS_URL, body)
    return Alarm(self, resp, assumed_control_panel_state)

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
