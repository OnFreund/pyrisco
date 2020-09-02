"""Implementation of a Risco Cloud connection."""

import aiohttp
import asyncio

LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"
SITE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/GetAll"
PIN_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/Login"
STATE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetState"
CONTROL_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/PartArm"
EVENTS_URL = (
    "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetEventLog"
)
BYPASS_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/SetZoneBypassStatus"

GROUP_ID_TO_NAME = ["A", "B", "C", "D"]

NUM_RETRIES = 3

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

    @property
    def bypassed(self):
        """Is the zone triggered."""
        return self._raw["status"] == 2


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
            self._partitions = {p["id"]: Partition(p) for p in self._raw["partitions"]}
        return self._partitions

    @property
    def zones(self):
        """Alarm zones."""
        if self._zones is None:
            self._zones = {z["zoneID"]: Zone(z) for z in self._raw["zones"]}
        return self._zones


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
        return Alarm(resp["state"]["status"])

    async def disarm(self, partition):
        """Disarm the alarm."""
        body = {
            "partitions": [{"id": partition, "armedState": 1}],
        }
        return Alarm(await self._site_post(CONTROL_URL, body))

    async def arm(self, partition):
        """Arm the alarm."""
        body = {
            "partitions": [{"id": partition, "armedState": 3}],
        }
        return Alarm(await self._site_post(CONTROL_URL, body))

    async def partial_arm(self, partition):
        """Partially-arm the alarm."""
        body = {
            "partitions": [{"id": partition, "armedState": 2}],
        }
        return Alarm(await self._site_post(CONTROL_URL, body))

    async def group_arm(self, partition, group):
        """Arm a specific group."""
        if isinstance(group, str):
            group = GROUP_ID_TO_NAME.index(group)

        body = {
            "partitions": [{"id": partition, "groups": [{"id": group, "state": 3}]}],
        }
        return Alarm(await self._site_post(CONTROL_URL, body))

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
        return Alarm(await self._site_post(BYPASS_URL, body))

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


class UnauthorizedError(Exception):
    """Exception to indicate an error in authorization."""


class CannotConnectError(Exception):
    """Exception to indicate an error in authorization."""


class OperationError(Exception):
    """Exception to indicate an error in operation."""
