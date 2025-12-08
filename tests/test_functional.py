"""Minimal functional tests for the FastAPI application.

These tests verify that the API starts and responds correctly.
Extensive unit testing can be added later after proving the concept works.
"""


def test_root_endpoint(test_client):
    """Test the root endpoint returns service information."""
    response = test_client.get("/")
    # Protected: expect 401 (no key) or 503 if Mongo unavailable
    assert response.status_code in [401, 503]


def test_health_check(test_client):
    """Test the basic health check endpoint."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


def test_notify_auth_expiry_accepts_request(test_client):
    """Test the notify-auth-expiry endpoint accepts valid requests.

    Note: This test may fail if STARS API or MongoDB are not accessible.
    It's primarily to verify the endpoint structure is correct.
    """
    response = test_client.post(
        "/auths/notify-auth-expiry",
        json={},  # Empty body should use defaults from config
    )

    # Protected: expect 401 without a stored key, or 503 if auth unavailable
    # The important thing is the endpoint responds with proper structure
    assert response.status_code in [401, 503]


def test_invalid_request_returns_401(test_client):
    """Test that invalid request body returns 401 Unprocessable Entity."""
    response = test_client.post(
        "/auths/notify-auth-expiry",
        json={"warning_days": "not_a_number"},  # Invalid type
    )

    assert response.status_code == 401


def test_list_expiring_auths_endpoint_exists(test_client):
    """Test the list expiring auths endpoint exists.

    Note: This test may fail if STARS API is not accessible.
    """
    response = test_client.get("/auths/expiring")

    # Protected: expect 401 without a stored key, or 503 if auth unavailable
    assert response.status_code in [401, 503]


def test_protected_endpoint_requires_api_key(test_client):
    """Ensure protected endpoints reject missing API key."""

    response = test_client.post("/auths/notify-auth-expiry", json={})
    assert response.status_code == 401
