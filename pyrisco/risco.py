"""Implementation of a Risco Cloud connection."""

import aiohttp


LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"
SITE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/GetAll"
PIN_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/Login"
STATE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetState"
CONTROL_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/PartArm"
EVENTS_URL = (
    "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetEventLog"
)

GROUP_ID_TO_NAME = ["A", "B", "C", "D"]


class Partition:
    """A representation of a Risco partition."""

    def __init__(self, raw):
        """Read partition from response."""
        self._raw = raw

    @property
    def id(self):
        """Partition ID number."""
        return self._raw["id"]

    @property
    def disarmed(self):
        """Is the partition disarmed."""
        return self._raw["armedState"] == 1

    @property
    def partially_armed(self):
        """Is the partition partially-armed."""
        return self._raw["armedState"] == 2

    @property
    def armed(self):
        """Is the partition armed."""
        return self._raw["armedState"] == 3

    @property
    def triggered(self):
        """Is the partition triggered."""
        return self._raw["alarmState"] == 1

    @property
    def exit_timeout(self):
        """Time remaining till armed."""
        return self._raw["exitDelayTO"]

    @property
    def arming(self):
        """Is the partition arming."""
        return self.exit_timeout > 0

    @property
    def groups(self):
        """Group arming status."""
        return {GROUP_ID_TO_NAME[g["id"]]: g["state"] == 3 for g in self._raw["groups"]}


class Zone:
    """A representation of a Risco zone."""

    def __init__(self, raw):
        """Read zone from response."""
        self._raw = raw

    @property
    def id(self):
        """Zone ID number."""
        return self._raw["zoneID"]

    @property
    def name(self):
        """Zone name."""
        return self._raw["zoneName"]

    @property
    def type(self):
        """Zone type."""
        return self._raw["zoneType"]

    @property
    def triggered(self):
        """Is the zone triggered."""
        return self._raw["status"] == 1


class Alarm:
    """A representation of a Risco alarm system."""

    def __init__(self, raw):
        """Read alarm from response."""
        self._raw = raw
        self._partitions = None
        self._zones = None

    @property
    def partitions(self):
        """Alarm partitions."""
        if self._partitions is None:
            self._partitions = [Partition(p) for p in self._raw["partitions"]]
        return self._partitions

    @property
    def zones(self):
        """Alarm zones."""
        if self._zones is None:
            self._zones = [Zone(z) for z in self._raw["zones"]]
        return self._zones


class Event:
    """A representation of a Risco event."""

    def __init__(self, raw):
        """Read event from response."""
        self._raw = raw

    @property
    def time(self):
        """Time the event was fired."""
        return self._raw["logTime"]

    @property
    def text(self):
        """Event text."""
        return self._raw["eventText"]

    @property
    def name(self):
        """Event name."""
        return self._raw["eventName"]

    @property
    def group(self):
        """Event group number."""
        return self._raw["group"]


class RiscoAPI:
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
        self._session = None
        self._created_session = False

    async def _authenticated_post(self, url, body, retry=True):
        headers = {
            "Content-Type": "application/json",
            "authorization": "Bearer " + self._access_token,
        }
        async with self._session.post(url, headers=headers, json=body) as resp:
            json = await resp.json()
            if json["status"] == 401:
                if retry:
                    await self.login()
                    await self._authenticated_post(url, body, False)
                else:
                    raise UnauthorizedError(json["errorText"])

            if "result" in json and json["result"] != 0:
                raise OperationError(f"result: {json['result']}")

            return json["response"]

    async def _login_user_pass(self):
        headers = {"Content-Type": "application/json"}
        body = {"userName": self._username, "password": self._password}
        try:
            async with self._session.post(LOGIN_URL, headers=headers, json=body) as resp:
                json = await resp.json()
                if json["status"] == 401:
                    raise UnauthorizedError("Invalid username or password")
                self._access_token = json["response"].get("accessToken")
        except aiohttp.client_exceptions.ClientConnectorError as e:
            raise CannotConnectError() from e

        if not self._access_token:
            raise UnauthorizedError("Invalid username or password")

    async def _login_site(self):
        resp = await self._authenticated_post(SITE_URL, {}, False)
        self._site_id = resp[0]["id"]

    async def _login_session(self):
        body = {"languageId": self._language, "pinCode": self._pin}
        url = PIN_URL % self._site_id
        resp = await self._authenticated_post(url, body, False)
        self._session_id = resp["sessionId"]

    async def _init_session(self, session):
        await self.close()
        if self._session is None:
            if session is None:
                self._session = aiohttp.ClientSession()
                self._created_session = True
            else:
                self._session = session

    async def close(self):
        """Close the connection."""
        if self._created_session == True and self._session is not None:
            await self._session.close()
            self._session = None
            self._created_session = False

    async def login(self, session=None):
        """Login to Risco Cloud."""
        await self._init_session(session)
        await self._login_user_pass()
        await self._login_site()
        await self._login_session()

    async def get_state(self):
        """Get partitions and zones."""
        url = STATE_URL % self._site_id
        body = {"fromControlPanel": True, "sessionToken": self._session_id}
        resp = await self._authenticated_post(url, body)
        return Alarm(resp["state"]["status"])

    async def disarm(self, partition):
        """Disarm the alarm."""
        url = CONTROL_URL % self._site_id
        body = {
            "partitions": [{"id": partition, "armedState": 1}],
            "fromControlPanel": True,
            "sessionToken": self._session_id,
        }
        return Alarm(await self._authenticated_post(url, body))

    async def arm(self, partition):
        """Arm the alarm."""
        url = CONTROL_URL % self._site_id
        body = {
            "partitions": [{"id": partition, "armedState": 3}],
            "fromControlPanel": True,
            "sessionToken": self._session_id,
        }
        return Alarm(await self._authenticated_post(url, body))

    async def partial_arm(self, partition):
        """Partially-arm the alarm."""
        url = CONTROL_URL % self._site_id
        body = {
            "partitions": [{"id": partition, "armedState": 2}],
            "fromControlPanel": True,
            "sessionToken": self._session_id,
        }
        return Alarm(await self._authenticated_post(url, body))

    async def group_arm(self, partition, group):
        """Arm a specific group."""
        if isinstance(group, str):
            group = GROUP_ID_TO_NAME.index(group)

        url = CONTROL_URL % self._site_id
        body = {
            "partitions": [{"id": partition, "groups": [{"id": group, "state": 3}]}],
            "fromControlPanel": True,
            "sessionToken": self._session_id,
        }
        return Alarm(await self._authenticated_post(url, body))

    async def get_events(self, newer_than, count=10):
        """Get event log."""
        url = EVENTS_URL % self._site_id
        body = {
            "count": count,
            "newerThan": newer_than,
            "offset": 0,
            "fromControlPanel": True,
            "sessionToken": self._session_id,
        }
        response = await self._authenticated_post(url, body)
        return [Event(e) for e in response["controlPanelEventsList"]]

    @property
    def site_id(self):
        """Site ID of the Alarm instance."""
        return self._site_id


class UnauthorizedError(Exception):
    """Exception to indicate an error in authorization."""

class CannotConnectError(Exception):
    """Exception to indicate an error in authorization."""

class OperationError(Exception):
    """Exception to indicate an error in operation."""
