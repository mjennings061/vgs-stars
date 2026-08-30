"""Pytest configuration and fixtures for testing."""

import os

# Set ALL required environment variables BEFORE any app imports,
# since config.py reads os.environ[] at import time.
os.environ.setdefault("STARS_URI", "https://test.stars.api/api")
os.environ.setdefault("STARS_API_KEY", "test_api_key")
os.environ.setdefault("STARS_ORG_UNIT_ID", "test_org_unit_id")
os.environ.setdefault("DATABASE_NOTIFICATIONS_COLLECTION", "auths_notification")
os.environ.setdefault("DATABASE_NOTIFICATION_BATCHES_COLLECTION", "auth_notification_batches")
os.environ.setdefault("DATABASE_USERS_COLLECTION", "users")
os.environ.setdefault("SENDGRID_API_KEY", "test_sendgrid_key")
os.environ.setdefault("SENDGRID_FROM_EMAIL", "test@example.com")
os.environ.setdefault("SENDGRID_FROM_NAME", "Test Sender")
os.environ.setdefault("EXPIRY_WARNING_DAYS", "30")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("API_KEY_HEADER_NAME", "X-API-Key")
os.environ.setdefault("CLOUD_TASKS_QUEUE_PATH", "projects/test/locations/test/queues/test")
os.environ.setdefault("CLOUD_TASKS_TARGET_URL", "https://test.example.com/api/send_notification")
os.environ.setdefault("CLOUD_TASKS_API_KEY", "test_cloud_tasks_key")
os.environ.setdefault("CLOUD_TASKS_DISPATCH_DELAY_SECONDS", "20")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI application."""
    client = TestClient(app)
    return client
