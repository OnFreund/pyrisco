import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
from pyrisco.cloud.risco_cloud import RiscoCloud, UnauthorizedError, OperationError, RetryableOperationError
from pyrisco.cloud.alarm import Alarm

LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"


def _make_sse_stream(*events):
  """Build an async iterator that yields SSE lines for the given events.

  Each event is a (event_type, data_dict) tuple.
  """
  lines = []
  for event_type, data in events:
    import json as _json
    lines.append(f"event: {event_type}\r\n".encode())
    lines.append(f"data: {_json.dumps(data)}\r\n".encode())
    lines.append(b"\r\n")

  async def _iter():
    for line in lines:
      yield line

  return _iter()


class TestRiscoCloud(unittest.IsolatedAsyncioTestCase):

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_login(self, MockClientSession, mock_authenticated_post):
    mock_session = MockClientSession.return_value
    mock_post = AsyncMock()
    mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_post)
    mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_post.json = AsyncMock(return_value={
      "status": 0,
      "response": {
        'accessToken': 'mock_access_token',
        'classVersion': 0,
        'expiresAt': '2025-02-01T20:40:03.6092768Z',
        'refreshToken': 'mock_access_refresh_token',
        'tokenType': 'Bearer'
      }
    })

    mock_authenticated_post.side_effect = [
      [{
        "id": "mock_site_id",
        "name": "mock_site_name",
        "siteUUID": "mock_site_uuid"
      }],
      {
        "sessionId": "mock_session_id"
      }
    ]

    risco_cloud = RiscoCloud("username", "password", "pin")
    await risco_cloud.login()

    self.assertEqual(risco_cloud._access_token, "mock_access_token")
    mock_session.post.assert_called_with(
      LOGIN_URL,
      headers={"Content-Type": "application/json", "User-Agent": "pyrisco/1.0"},
      json={"userName": "username", "password": "password"}
    )
    mock_authenticated_post.assert_called()

  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_login_unauthorized(self, MockClientSession):
    mock_session = MockClientSession.return_value
    mock_post = AsyncMock()
    mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_post)
    mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_post.json = AsyncMock(return_value={
      "status": 0,
      "response": {
        'currentLoginAttempt': 2,
        'errorText': 'invalid custom credential',
        'errorTextCodeID': '0',
        'maxLoginAttempts': 5,
        'response': None,
        'status': 401,
        'validationErrors': None
      }
    })

    risco_cloud = RiscoCloud("username", "password", "pin")
    with self.assertRaises(UnauthorizedError):
      await risco_cloud.login()

    mock_session.post.assert_called_with(
      LOGIN_URL,
      headers={"Content-Type": "application/json", "User-Agent": "pyrisco/1.0"},
      json={"userName": "username", "password": "password"}
    )

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_ok(self, mock_authenticated_post):
    mock_authenticated_post.side_effect = [
      {
        "state": {"status": {"partitions": [], "zones": []}}
      }
    ]

    risco_cloud = RiscoCloud("username", "password", "pin")
    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    state = await risco_cloud.get_state()

    self.assertEqual(state.partitions, {})
    self.assertEqual(state.zones, {})
    self.assertFalse(state.assumed_control_panel_state)
    self.assertEqual(mock_authenticated_post.call_count, 1)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_retries(self, mock_authenticated_post):
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
      # Successful response on the third call
      {
        "state": {"status": {"partitions": [], "zones": []}}
      }
    ]

    risco_cloud = RiscoCloud("username", "password", "pin")

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    state = await risco_cloud.get_state()

    self.assertEqual(state.partitions, {})
    self.assertEqual(state.zones, {})
    self.assertTrue(state.assumed_control_panel_state)
    self.assertEqual(mock_authenticated_post.call_count, 3)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_retries_fails(self, mock_authenticated_post):
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
    ]

    risco_cloud = RiscoCloud("username", "password", "pin")

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    with self.assertRaises(OperationError):
      await risco_cloud.get_state()

    self.assertEqual(mock_authenticated_post.call_count, 3)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_fails(self, mock_authenticated_post):
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
      OperationError("Operation error")
    ]

    risco_cloud = RiscoCloud("username", "password", "pin")

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    with self.assertRaises(OperationError):
      await risco_cloud.get_state()

    self.assertEqual(mock_authenticated_post.call_count, 3)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_fallback_to_cloud(self, mock_authenticated_post):
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      # Successful response on the second call
      {
          "state": {"status": {"partitions": [], "zones": []}}
      }
    ]

    risco_cloud = RiscoCloud("username", "password", "pin")

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    state = await risco_cloud.get_state()

    self.assertEqual(state.partitions, {})
    self.assertEqual(state.zones, {})
    self.assertTrue(state.assumed_control_panel_state)
    self.assertEqual(mock_authenticated_post.call_count, 2)
    self.assertTrue(mock_authenticated_post.call_args_list[0][0][1]['fromControlPanel'])
    self.assertFalse(mock_authenticated_post.call_args_list[1][0][1]['fromControlPanel'])

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_notifies_handler_immediately(self, MockClientSession, mock_site_post):
    """Handler should be called right after SSE connects, before any SSE messages."""
    state_payload = {"partitions": [], "zones": []}
    mock_site_post.return_value = ({"state": {"status": state_payload}}, False)

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream()  # no SSE messages
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received = []
    async def on_state(alarm):
      received.append(alarm)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_state_handler(on_state)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received), 1)
    self.assertIsInstance(received[0], Alarm)
    self.assertEqual(mock_site_post.call_count, 1)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_calls_handler(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    # Two calls: initial state fetch + state fetch triggered by SSE message
    mock_site_post.return_value = ({"state": {"status": state_payload}}, False)

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {"LastStatusUpdate": "2024-01-01T12:00:00Z"}),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received = []
    async def on_state(alarm):
      received.append(alarm)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_state_handler(on_state)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received), 2)  # initial fetch + SSE-triggered fetch
    self.assertIsInstance(received[0], Alarm)
    self.assertEqual(mock_site_post.call_count, 2)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_no_update_same_timestamp(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    mock_site_post.return_value = ({"state": {"status": state_payload}}, False)

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {"LastStatusUpdate": "2024-01-01T12:00:00Z"}),
      ("runtimeUpdate", {"LastStatusUpdate": "2024-01-01T12:00:00Z"}),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    # initial fetch + first SSE message; second SSE message has same timestamp so skipped
    self.assertEqual(mock_site_post.call_count, 2)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  async def test_get_state_returns_cached(self, mock_site_post):
    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"

    cached_alarm = Alarm(risco_cloud, {"partitions": [], "zones": []}, False)
    risco_cloud._latest_state = cached_alarm

    result = await risco_cloud.get_state()

    self.assertIs(result, cached_alarm)
    mock_site_post.assert_not_called()

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_skips_fetch_when_offline(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    mock_site_post.return_value = ({"state": {"status": state_payload}}, False)

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {"LastStatusUpdate": "2024-01-01T12:00:00Z", "IsOffline": True}),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    # Only the initial fetch; the SSE message is skipped because IsOffline=True
    self.assertEqual(mock_site_post.call_count, 1)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_event_handler_fires_without_status_update(self, MockClientSession, mock_site_post):
    """LastEventUpdated should trigger the event handler even when LastStatusUpdate is absent."""
    state_payload = {"partitions": [], "zones": []}
    event_payload = {"controlPanelEventsList": []}
    mock_site_post.side_effect = [
      ({"state": {"status": state_payload}}, False),  # initial state fetch
      (event_payload, False),                          # event fetch from SSE message
    ]

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {"LastEventUpdated": "2024-01-01T12:00:00Z"}),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received_events = []
    async def on_event(events):
      received_events.append(events)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_event_handler(on_event)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received_events), 1)
    self.assertEqual(mock_site_post.call_count, 2)  # initial state fetch + event fetch

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_calls_error_handler(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    mock_site_post.return_value = ({"state": {"status": state_payload}}, False)

    mock_session = MockClientSession.return_value
    error = RuntimeError("stream broken")

    async def _failing_content():
      raise error
      yield  # make it an async generator

    mock_resp = MagicMock()
    mock_resp.content = _failing_content()
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received_errors = []
    async def on_error(err):
      received_errors.append(err)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_error_handler(on_error)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received_errors), 1)
    self.assertIs(received_errors[0], error)


  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_calls_event_handler_with_null_response(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    mock_site_post.side_effect = [
      ({"state": {"status": state_payload}}, False),  # initial state fetch
      ({"state": {"status": state_payload}}, False),  # state fetch from SSE message
      (None, False),                                   # event fetch returns null
    ]

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {
        "LastStatusUpdate": "2024-01-01T12:00:00Z",
        "LastEventUpdated": "2024-01-01T12:00:00Z",
      }),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received_events = []
    async def on_event(events):
      received_events.append(events)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_event_handler(on_event)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received_events), 1)
    self.assertEqual(received_events[0], [])

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_calls_event_handler(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    event_payload = {"controlPanelEventsList": []}
    mock_site_post.side_effect = [
      ({"state": {"status": state_payload}}, False),  # initial state fetch
      ({"state": {"status": state_payload}}, False),  # state fetch from SSE message
      (event_payload, False),                          # event fetch
    ]

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {
        "LastStatusUpdate": "2024-01-01T12:00:00Z",
        "LastEventUpdated": "2024-01-01T12:00:00Z",
      }),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received_events = []
    async def on_event(events):
      received_events.append(events)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_event_handler(on_event)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received_events), 1)
    self.assertIsInstance(received_events[0], list)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._site_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_subscribe_states_no_event_handler_when_no_last_event_updated(self, MockClientSession, mock_site_post):
    state_payload = {"partitions": [], "zones": []}
    mock_site_post.return_value = ({"state": {"status": state_payload}}, False)

    mock_session = MockClientSession.return_value
    mock_resp = MagicMock()
    mock_resp.content = _make_sse_stream(
      ("runtimeUpdate", {"LastStatusUpdate": "2024-01-01T12:00:00Z"}),
    )
    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_get_cm

    received_events = []
    async def on_event(events):
      received_events.append(events)

    risco_cloud = RiscoCloud("username", "password", "pin")
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"
    risco_cloud._session = mock_session
    risco_cloud.add_event_handler(on_event)

    await risco_cloud.subscribe_states()
    await risco_cloud._subscription_task

    self.assertEqual(len(received_events), 0)
    self.assertEqual(mock_site_post.call_count, 2)  # initial state fetch + state fetch from SSE, no event fetch


if __name__ == '__main__':
  unittest.main()
