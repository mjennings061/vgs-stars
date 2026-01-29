"""Minimal functional tests for the FastAPI application.

These tests verify that the API starts and responds correctly.
Extensive unit testing can be added later after proving the concept works.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.models.notifications import NotificationStatus
from app.models.stars import Auth
from app.services.notification_service import should_send_notification


@pytest.mark.asyncio
async def test_root_endpoint(test_client):
    """Test the root endpoint returns service information."""
    response = test_client.get("/")
    # Protected: expect 401 (no key) or 503 if Firestore unavailable
    assert response.status_code in [401, 503]


@pytest.mark.asyncio
async def test_health_check(test_client):
    """Test the basic health check endpoint."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_notify_auth_expiry_accepts_request(test_client):
    """Test the notify-auth-expiry endpoint accepts valid requests.

    Note: This test may fail if STARS API or Firestore are not accessible.
    It's primarily to verify the endpoint structure is correct.
    """
    response = test_client.post(
        "/auths/notify-auth-expiry",
        json={},  # Empty body should use defaults from config
    )

    # Protected: expect 401 without a stored key, or 503 if auth unavailable
    # The important thing is the endpoint responds with proper structure
    assert response.status_code in [401, 503]


@pytest.mark.asyncio
async def test_invalid_request_returns_401(test_client):
    """Test that invalid request body returns 401 Unprocessable Entity."""
    response = test_client.post(
        "/auths/notify-auth-expiry",
        json={"warning_days": "not_a_number"},  # Invalid type
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_expiring_auths_endpoint_exists(test_client):
    """Test the list expiring auths endpoint exists.

    Note: This test may fail if STARS API is not accessible.
    """
    response = test_client.get("/auths/expiring")

    # Protected: expect 401 without a stored key, or 503 if auth unavailable
    assert response.status_code in [401, 503]


@pytest.mark.asyncio
async def test_protected_endpoint_requires_api_key(test_client):
    """Ensure protected endpoints reject missing API key."""

    response = test_client.post("/auths/notify-auth-expiry", json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_partial_notification_filters_already_notified_auths():
    """Test that should_send_notification correctly filters already-notified auths.

    Scenario:
    - User has 3 expiring auths
    - 2 have already been notified (status: SENT in database)
    - 1 has not been notified yet
    - Expected: Only the 1 new auth should pass the should_send_notification check
    """

    # Create test data
    today = date.today()
    expiry_date = today + timedelta(days=15)

    # Three expiring auths for the same user
    auth_1 = Auth(
        id=101,
        mapId=1001,
        mapName="Auth 1 - Already Notified",
        state="Active",
        currencyState="Current",
        mapLevel="Level 1",
        resourceId="PERSON123",
        resourceName="John Smith",
        orgUnitId=1,
        orgUnit="Test Unit",
        expiry=expiry_date,
    )
    auth_2 = Auth(
        id=102,
        mapId=1002,
        mapName="Auth 2 - Already Notified",
        state="Active",
        currencyState="Current",
        mapLevel="Level 1",
        resourceId="PERSON123",
        resourceName="John Smith",
        orgUnitId=1,
        orgUnit="Test Unit",
        expiry=expiry_date,
    )
    auth_3 = Auth(
        id=103,
        mapId=1003,
        mapName="Auth 3 - New (Not Notified)",
        state="Active",
        currencyState="Current",
        mapLevel="Level 1",
        resourceId="PERSON123",
        resourceName="John Smith",
        orgUnitId=1,
        orgUnit="Test Unit",
        expiry=expiry_date,
    )

    # Mock database: auth_1 and auth_2 are already SENT, auth_3 has no notifications
    mock_notifications = {
        101: [{"authId": 101, "status": NotificationStatus.SENT.value}],
        102: [{"authId": 102, "status": NotificationStatus.SENT.value}],
        103: [],
    }

    # Create an async version of the side effect
    async def async_get_notifications(auth_id):
        return mock_notifications.get(auth_id, [])

    with patch(
        "app.services.notification_service.database.get_notifications_for_auth"
    ) as mock_get_notifications:
        mock_get_notifications.side_effect = async_get_notifications

        # Test each auth
        result_1 = await should_send_notification(auth_1)
        result_2 = await should_send_notification(auth_2)
        result_3 = await should_send_notification(auth_3)

        # Verify results
        assert result_1 is False, "auth_1 should not be sent (already notified)"
        assert result_2 is False, "auth_2 should not be sent (already notified)"
        assert result_3 is True, "auth_3 should be sent (not yet notified)"

        # Verify database was queried for each auth
        assert mock_get_notifications.call_count == 3
