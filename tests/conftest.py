"""Pytest configuration and fixtures for testing."""

# Imports sit below the env vars on purpose, so this cannot be tidied away.
# pylint: disable=wrong-import-position
# Fixtures shadow their own names by design in pytest.
# pylint: disable=redefined-outer-name

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

import hashlib  # noqa: E402
import secrets  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from google.cloud import firestore  # noqa: E402

from app.config import (  # noqa: E402
    DATABASE_USERS_COLLECTION,
    ROSTER_PEOPLE_COLLECTION,
    ROSTER_SESSIONS_COLLECTION,
    SCOPE_ROSTER_READ,
    STARS_ORG_UNIT_ID,
)
from app.main import app  # noqa: E402
from app.models.roster import Role  # noqa: E402
from app.services import api_keys, database  # noqa: E402

READ_KEY = "test-roster-read-key"
HEADERS = {"X-API-Key": READ_KEY}
ADMIN_ID = "p-1043"
ADMIN_NAME = "J Doe"
ADMIN_EMAIL = "jdoe@example.com"
MEMBER_ID = "p-2210"
MEMBER_NAME = "A Rae"


@pytest.fixture(autouse=True)
def _fresh_firestore_client():
    """Never let a Firestore client outlive the event loop that built it."""
    database._client = None  # pylint: disable=protected-access
    yield
    database._client = None  # pylint: disable=protected-access


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI application."""
    return TestClient(app)


def _wipe() -> None:
    """Drop every document in the emulator."""
    requests.delete(
        f"http://{os.environ['FIRESTORE_EMULATOR_HOST']}/emulator/v1"
        f"/projects/{os.environ['GOOGLE_CLOUD_PROJECT']}"
        "/databases/(default)/documents",
        timeout=10,
    )


def _person(person_id: str, name: str, role: Role, email: str | None) -> dict:
    """Build a seeded roster person.

    Args:
        person_id: STARS person identifier.
        name: Their name.
        role: What they are allowed to do.
        email: Their address, or None.

    Returns:
        The document to write.
    """
    return {
        "squadronId": STARS_ORG_UNIT_ID,
        "personId": person_id,
        "name": name,
        "email": email,
        "initials": "".join(part[0] for part in name.split()),
        "rank": "Fg Off",
        "instructCat": "B2",
        "role": role.value,
    }


@pytest.fixture
def seed():
    """Reset the emulator and seed a read key, an admin and a member."""
    _wipe()
    store = firestore.Client()
    store.collection(DATABASE_USERS_COLLECTION).document("read-key").set(
        {
            "name": "dashboard",
            "api_key": api_keys.hash_api_key(READ_KEY),
            "scopes": [SCOPE_ROSTER_READ],
        }
    )
    people = store.collection(ROSTER_PEOPLE_COLLECTION)
    people.document(f"{STARS_ORG_UNIT_ID}:{ADMIN_ID}").set(
        _person(ADMIN_ID, ADMIN_NAME, Role.ADMIN, ADMIN_EMAIL)
    )
    people.document(f"{STARS_ORG_UNIT_ID}:{MEMBER_ID}").set(
        _person(MEMBER_ID, MEMBER_NAME, Role.MEMBER, "arae@example.com")
    )
    return store


@pytest.fixture
def sent(mocker):
    """Patch Resend so no test can ever send an email."""
    return mocker.patch("app.services.email_service.resend.Emails.send")


@pytest.fixture
def client():
    """A context-managed client, so one event loop serves the whole flow."""
    with TestClient(app) as test_client:
        yield test_client


def _bearer(store, person_id: str, name: str, role: Role) -> dict:
    """Open a session straight in Firestore, skipping the emailed code.

    Args:
        store: The synchronous Firestore client from ``seed``.
        person_id: Who the session belongs to.
        name: Their name.
        role: What they are allowed to do.

    Returns:
        The Authorization header carrying the new token.
    """
    token = secrets.token_urlsafe(32)
    store.collection(ROSTER_SESSIONS_COLLECTION).document(
        hashlib.sha256(token.encode("utf-8")).hexdigest()
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "personId": person_id,
            "name": name,
            "role": role.value,
            "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth(seed):
    """Authorization header for a signed-in admin."""
    return _bearer(seed, ADMIN_ID, ADMIN_NAME, Role.ADMIN)


@pytest.fixture
def member_auth(seed):
    """Authorization header for a signed-in member."""
    return _bearer(seed, MEMBER_ID, MEMBER_NAME, Role.MEMBER)
