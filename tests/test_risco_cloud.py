import unittest
from unittest.mock import patch, AsyncMock
from pyrisco.cloud.risco_cloud import RiscoCloud, UnauthorizedError, OperationError, RetryableOperationError

LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"


class TestRiscoCloud(unittest.IsolatedAsyncioTestCase):

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_login(self, MockClientSession, mock_authenticated_post):
    # Mock the aiohttp session and its methods
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

    # Mock the _authenticated_post method
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

    # Create an instance of RiscoCloud
    risco_cloud = RiscoCloud("username", "password", "pin")

    # Run the login method
    await risco_cloud.login()

    # Check if the access token is set correctly
    self.assertEqual(risco_cloud._access_token, "mock_access_token")

    # Check if the post method was called with the correct URL and headers
    mock_session.post.assert_called_with(
      LOGIN_URL,
      headers={"Content-Type": "application/json"},
      json={"userName": "username", "password": "password"}
    )

    # Check if the _authenticated_post method was called
    mock_authenticated_post.assert_called()

  @patch('pyrisco.cloud.risco_cloud.aiohttp.ClientSession')
  async def test_login_unauthorized(self, MockClientSession):
    # Mock the aiohttp session and its methods
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

    # Create an instance of RiscoCloud
    risco_cloud = RiscoCloud("username", "password", "pin")

    with self.assertRaises(UnauthorizedError):
      # Run the login method
      await risco_cloud.login()

    # Check if the post method was called with the correct URL and headers
    mock_session.post.assert_called_with(
      LOGIN_URL,
      headers={"Content-Type": "application/json"},
      json={"userName": "username", "password": "password"}
    )

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_retries(self, mock_authenticated_post):
    # Mock the _site_post method to raise RetryableOperationError for the first two calls
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
      # Successful response on the third call
      {
        "state": {"partitions": [], "zones": [], "status": {}}
      }
    ]

    # Create an instance of RiscoCloud
    risco_cloud = RiscoCloud("username", "password", "pin", from_control_panel=True)

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"

    # Run the get_state method
    state = await risco_cloud.get_state()

    # Check if the state is returned correctly
    self.assertEqual(state.partitions, {})
    self.assertEqual(state.zones, {})
    self.assertTrue(state.is_from_control_panel)

    # Check if the _site_post method was called three times
    self.assertEqual(mock_authenticated_post.call_count, 3)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_retries_fails(self, mock_authenticated_post):
    # Mock the _site_post method to raise RetryableOperationError for the first two calls
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
    ]

    # Create an instance of RiscoCloud
    risco_cloud = RiscoCloud("username", "password", "pin", from_control_panel=True)

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"

    # Run the get_state method
    with self.assertRaises(OperationError):
      await risco_cloud.get_state()

    # Check if the _site_post method was called three times
    self.assertEqual(mock_authenticated_post.call_count, 3)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_fails(self, mock_authenticated_post):
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      RetryableOperationError("Retryable error"),
      OperationError("Operation error")
    ]

    # Create an instance of RiscoCloud
    risco_cloud = RiscoCloud("username", "password", "pin")

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"

    with self.assertRaises(OperationError):
      # Run the get_state method
      await risco_cloud.get_state()

    # Check if the _site_post method was called three times
    self.assertEqual(mock_authenticated_post.call_count, 3)

  @patch('pyrisco.cloud.risco_cloud.RiscoCloud._authenticated_post', new_callable=AsyncMock)
  async def test_get_state_with_fallback_to_cloud(self, mock_authenticated_post):
    # Mock the _site_post method to raise RetryableOperationError for the first call
    mock_authenticated_post.side_effect = [
      RetryableOperationError("Retryable error"),
      # Successful response on the second call with fallback to cloud
      {
        "state": {"partitions": [], "zones": [], "status": {}}
      }
    ]

    # Create an instance of RiscoCloud
    risco_cloud = RiscoCloud("username", "password", "pin", from_control_panel=True, fallback_to_cloud=True)

    # Set the access token to avoid login
    risco_cloud._access_token = "mock_access_token"
    risco_cloud._site_id = "mock_site_id"
    risco_cloud._session_id = "mock_session_id"

    # Run the get_state method
    state = await risco_cloud.get_state()

    # Check if the state is returned correctly
    self.assertEqual(state.partitions, {})
    self.assertEqual(state.zones, {})
    self.assertFalse(state.is_from_control_panel)

    # Check if the _site_post method was called twice
    self.assertEqual(mock_authenticated_post.call_count, 2)

    # Check if the _site_post calls contain the correct from_control_panel value
    self.assertTrue(mock_authenticated_post.call_args_list[0][0][1]['fromControlPanel'])
    self.assertFalse(mock_authenticated_post.call_args_list[1][0][1]['fromControlPanel'])


if __name__ == '__main__':
  unittest.main()
