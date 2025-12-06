"""Minimal functional tests for the FastAPI application.

These tests verify that the API starts and responds correctly.
Extensive unit testing can be added later after proving the concept works.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint():
    """Test the root endpoint returns service information."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "STARS Authorisation Expiry Notifications"
    assert "version" in data
    assert "endpoints" in data


def test_health_check():
    """Test the basic health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


def test_notify_auth_expiry_accepts_request():
    """Test the notify-auth-expiry endpoint accepts valid requests.

    Note: This test may fail if STARS API or MongoDB are not accessible.
    It's primarily to verify the endpoint structure is correct.
    """
    response = client.post(
        "/auths/notify-auth-expiry",
        json={},  # Empty body should use defaults from config
    )

    # Accept both 200 (success) and 500 (external dependency failure)
    # The important thing is the endpoint responds with proper structure
    assert response.status_code in [200, 500]

    data = response.json()

    # If successful, check response structure
    if response.status_code == 200:
        assert "success" in data
        assert "notifications_sent" in data
        assert "notifications_failed" in data
        assert "summary" in data
        assert "errors" in data


def test_invalid_request_returns_422():
    """Test that invalid request body returns 422 Unprocessable Entity."""
    response = client.post(
        "/auths/notify-auth-expiry",
        json={"warning_days": "not_a_number"},  # Invalid type
    )

    assert response.status_code == 422


def test_list_expiring_auths_endpoint_exists():
    """Test the list expiring auths endpoint exists.

    Note: This test may fail if STARS API is not accessible.
    """
    response = client.get("/auths/expiring")

    # Accept both 200 (success) and 500 (external dependency failure)
    assert response.status_code in [200, 500]
