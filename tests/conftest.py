"""Pytest configuration and fixtures for testing."""

# Imports sit below the env vars on purpose, so this cannot be tidied away.
# pylint: disable=wrong-import-position

import os

# Env vars must precede app imports, since config.py reads os.environ at import.
os.environ.setdefault("STARS_URI", "https://test.stars.api/api")
os.environ.setdefault("STARS_API_KEY", "test_api_key")
os.environ.setdefault("STARS_ORG_UNIT_ID", "test_org_unit_id")
os.environ.setdefault("DATABASE_NOTIFICATIONS_COLLECTION", "auths_notification")
os.environ.setdefault(
    "DATABASE_NOTIFICATION_BATCHES_COLLECTION", "auth_notification_batches"
)
os.environ.setdefault("DATABASE_USERS_COLLECTION", "users")
os.environ.setdefault("RESEND_API_KEY", "test_resend_key")
os.environ.setdefault("EMAIL_FROM", "Test Sender <test@example.com>")
os.environ.setdefault("EXPIRY_WARNING_DAYS", "30")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("API_KEY_HEADER_NAME", "X-API-Key")
os.environ.setdefault(
    "CLOUD_TASKS_QUEUE_PATH", "projects/test/locations/test/queues/test"
)
os.environ.setdefault(
    "CLOUD_TASKS_TARGET_URL", "https://test.example.com/api/send_notification"
)
os.environ.setdefault("CLOUD_TASKS_API_KEY", "test_cloud_tasks_key")
os.environ.setdefault("CLOUD_TASKS_DISPATCH_DELAY_SECONDS", "20")
# Only used against the Firestore emulator, which needs a project to namespace by.
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "vgs-stars-test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import database  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_firestore_client():
    """Never let a Firestore client outlive the event loop that built it."""
    database._client = None  # pylint: disable=protected-access
    yield
    database._client = None  # pylint: disable=protected-access


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI application."""
    client = TestClient(app)
    return client
