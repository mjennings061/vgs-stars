"""End-to-end tests against a deployed service, skipped unless it is configured.

Run with:
    E2E_BASE_URL=https://... E2E_ROSTER_KEY=... STARS_ORG_UNIT_ID=... \
        GOOGLE_CLOUD_PROJECT=vgs-stars-dev poetry run pytest tests/test_e2e.py

Writes go to a dummy admin and a far-future month, so no real person is touched.
"""

# Fixtures shadow their own names by design in pytest.
# pylint: disable=redefined-outer-name

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
import requests
from google.cloud import firestore

from app.config import (
    ROSTER_MONTHS_COLLECTION,
    ROSTER_PEOPLE_COLLECTION,
    ROSTER_SESSIONS_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import Role
from app.services import roster_auth

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
ROSTER_KEY = os.environ.get("E2E_ROSTER_KEY", "")
HEADERS = {"X-API-Key": ROSTER_KEY}
# Cloud Run scales to zero, so the first call of a run pays a cold start.
TIMEOUT = 30

# conftest.py sets this placeholder on import, so the real squadron must be exported.
PLACEHOLDER_SQUADRON = "test_org_unit_id"

PERSON_ID = "test-roster-01"
PERSON_NAME = "Roster Test"
# Resend's success simulator, so the mail path runs without reaching a real inbox.
PERSON_EMAIL = "delivered@resend.dev"

MONTH = "2099-01"
WEEKEND = "2099-01-03"
WEEKDAY = "2099-01-05"

pytestmark = pytest.mark.skipif(
    not (BASE_URL and ROSTER_KEY),
    reason="needs E2E_BASE_URL and E2E_ROSTER_KEY for a deployed service",
)


def _url(path: str) -> str:
    """Build a full URL for a path on the deployed service.

    Args:
        path: Path below the service root, starting with a slash.

    Returns:
        The absolute URL.
    """
    return f"{BASE_URL}{path}"


def _get(path: str, **kwargs) -> requests.Response:
    """GET a path on the deployed service.

    Args:
        path: Path below the service root, starting with a slash.
        **kwargs: Passed through to requests, such as headers.

    Returns:
        The HTTP response.
    """
    return requests.get(_url(path), timeout=TIMEOUT, **kwargs)


@pytest.fixture(scope="module")
def admin_auth():
    """Seed a dummy admin and a session for it, then clear up after the writes.

    A session cannot be earned here, since the code only exists in an email and
    as a hash, so it is written straight to Firestore as the emulator tests do.

    Yields:
        The Authorization header carrying the session token.
    """
    if STARS_ORG_UNIT_ID == PLACEHOLDER_SQUADRON:
        pytest.skip("export the real STARS_ORG_UNIT_ID for the write tests")

    store = firestore.Client()
    months = store.collection(ROSTER_MONTHS_COLLECTION)
    month_key = f"{STARS_ORG_UNIT_ID}:{MONTH}"

    # A month leaked by a killed run would otherwise 409 the create test.
    months.document(month_key).delete()

    store.collection(ROSTER_PEOPLE_COLLECTION).document(
        roster_auth.person_key(PERSON_ID)
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "personId": PERSON_ID,
            "name": PERSON_NAME,
            "email": PERSON_EMAIL,
            "role": Role.ADMIN.value,
        },
        merge=True,
    )

    token = secrets.token_urlsafe(32)
    session = store.collection(ROSTER_SESSIONS_COLLECTION).document(
        hashlib.sha256(token.encode("utf-8")).hexdigest()
    )
    session.set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "personId": PERSON_ID,
            "name": PERSON_NAME,
            "role": Role.ADMIN.value,
            "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )

    yield {"Authorization": f"Bearer {token}"}

    session.delete()
    months.document(month_key).delete()


def test_ready():
    """The revision boots and reaches real Firestore with real credentials."""
    response = _get("/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"


def test_people_needs_a_key():
    """The roster is not readable without a key."""
    assert _get("/roster/people").status_code == 401


@pytest.mark.usefixtures("admin_auth")
def test_people():
    """The real key resolves and the squadron comes back without any emails."""
    response = _get("/roster/people", headers=HEADERS)
    assert response.status_code == 200, response.text
    people = response.json()["people"]
    assert PERSON_ID in [person["personId"] for person in people]
    assert not [person for person in people if "email" in person]


def test_months():
    """Months are readable with the same key."""
    response = _get("/roster/months", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["months"], list)


def test_session_required():
    """A signed-in-only route refuses an anonymous caller."""
    assert _get("/roster/auth/me").status_code == 401


@pytest.mark.usefixtures("admin_auth")
def test_sign_in_sends_a_code():
    """A code reaches Resend, and a wrong code is refused."""
    response = requests.post(
        _url("/roster/auth/request-code"),
        json={"personId": PERSON_ID},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    if response.status_code == 429:
        pytest.skip("three code requests already made this hour")
    assert response.status_code == 202, response.text

    nonce = response.json()["nonce"]
    assert nonce

    wrong = requests.post(
        _url("/roster/auth/verify-code"),
        json={"nonce": nonce, "code": "000000", "rememberDevice": False},
        timeout=TIMEOUT,
    )
    assert wrong.status_code == 401, wrong.text


def test_create_month_needs_a_session():
    """A read key alone cannot open a month."""
    response = requests.post(
        _url("/roster/months"),
        json={"month": MONTH},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    assert response.status_code == 401, response.text


# The write tests below run in file order, each building on the month before it.
def test_create_month(admin_auth):
    """An admin opens a month, pre-filled with its weekends."""
    response = requests.post(
        _url("/roster/months"),
        json={"month": MONTH},
        headers=admin_auth,
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["month"] == MONTH
    assert body["freezeAt"] == "2098-12-18"
    assert body["frozen"] is False
    assert WEEKEND in body["dates"]
    assert WEEKDAY not in body["dates"]

    listed = _get("/roster/months", headers=HEADERS)
    assert MONTH in listed.json()["months"]


def test_patch_dates(admin_auth):
    """Adding and removing dates lands on the stored month."""
    response = requests.patch(
        _url(f"/roster/months/{MONTH}/dates"),
        json={"add": [WEEKDAY], "remove": [WEEKEND]},
        headers=admin_auth,
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text

    dates = response.json()["dates"]
    assert WEEKDAY in dates
    assert WEEKEND not in dates

    assert _get(f"/roster/months/{MONTH}", headers=HEADERS).json()["dates"] == dates


def test_patch_freeze(admin_auth):
    """Moving the freeze date lands on the stored month."""
    response = requests.patch(
        _url(f"/roster/months/{MONTH}/freeze"),
        json={"freezeAt": "2098-12-01"},
        headers=admin_auth,
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text
    assert response.json()["freezeAt"] == "2098-12-01"
