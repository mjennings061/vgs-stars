"""Behavioural tests for the squadron list and flying months."""

# Fixtures shadow their own names by design in pytest.
# pylint: disable=redefined-outer-name

import os
from datetime import date, timedelta

import pytest

from app.config import (
    DATABASE_USERS_COLLECTION,
    ROSTER_MONTHS_COLLECTION,
    SCOPE_STARS,
    STARS_ORG_UNIT_ID,
)
from app.services import api_keys
from tests.conftest import ADMIN_EMAIL, ADMIN_NAME, HEADERS, MEMBER_NAME

pytestmark = pytest.mark.skipif(
    "FIRESTORE_EMULATOR_HOST" not in os.environ,
    reason="needs the Firestore emulator: gcloud emulators firestore start",
)

MONTH = "2027-01"
FREEZE_AT = "2026-12-18"

# Hardcoded, so a wrong weekend rule cannot agree with itself.
WEEKENDS = [
    "2027-01-02",
    "2027-01-03",
    "2027-01-09",
    "2027-01-10",
    "2027-01-16",
    "2027-01-17",
    "2027-01-23",
    "2027-01-24",
    "2027-01-30",
    "2027-01-31",
]

ADMIN_ROUTES = [
    ("post", "/roster/months", {"month": MONTH}),
    ("patch", f"/roster/months/{MONTH}/dates", {"add": []}),
    ("patch", f"/roster/months/{MONTH}/freeze", {"freezeAt": FREEZE_AT}),
]


def _create(client, admin_auth, month: str = MONTH):
    """Open a month as an admin.

    Args:
        client: The test client.
        admin_auth: An admin's Authorization header.
        month: Year and month to open.

    Returns:
        The HTTP response.
    """
    return client.post("/roster/months", json={"month": month}, headers=admin_auth)


def _freeze(client, admin_auth, freeze_at: str):
    """Move a month's freeze date as an admin.

    Args:
        client: The test client.
        admin_auth: An admin's Authorization header.
        freeze_at: The new freeze date.

    Returns:
        The HTTP response.
    """
    return client.patch(
        f"/roster/months/{MONTH}/freeze",
        json={"freezeAt": freeze_at},
        headers=admin_auth,
    )


@pytest.mark.usefixtures("seed")
def test_people_never_returns_email(client):
    """The grid's rows, with no address anywhere in the payload."""
    response = client.get("/roster/people", headers=HEADERS)
    assert response.status_code == 200

    people = response.json()["people"]
    assert [person["name"] for person in people] == [MEMBER_NAME, ADMIN_NAME]
    assert people[0]["instructCat"] == "B2"
    assert people[0]["rank"] == "Fg Off"
    assert all("email" not in person for person in people)
    assert ADMIN_EMAIL not in response.text


def test_roster_reads_reject_a_stars_key(client, seed):
    """A key scoped for STARS must not reach the roster."""
    seed.collection(DATABASE_USERS_COLLECTION).document("stars-key").set(
        {
            "name": "stars",
            "api_key": api_keys.hash_api_key("test-stars-key"),
            "scopes": [SCOPE_STARS],
        }
    )

    for path in ("/roster/people", "/roster/months"):
        response = client.get(path, headers={"X-API-Key": "test-stars-key"})
        assert response.status_code == 403


def test_create_month_makes_every_weekend(client, admin_auth):
    """A new month arrives with its Saturdays, Sundays and freeze date."""
    response = _create(client, admin_auth)
    assert response.status_code == 200

    body = response.json()
    assert body["month"] == MONTH
    assert body["dates"] == WEEKENDS
    assert body["freezeAt"] == FREEZE_AT


def test_create_month_twice_is_409(client, admin_auth):
    """Two admins cannot both open January."""
    assert _create(client, admin_auth).status_code == 200
    assert _create(client, admin_auth).status_code == 409


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_refuse_members_and_strangers(
    client, member_auth, method, path, body
):
    """Signed out is 401, signed in without the role is 403."""
    signed_out = getattr(client, method)(path, json=body)
    as_member = getattr(client, method)(path, json=body, headers=member_auth)

    assert signed_out.status_code == 401
    assert as_member.status_code == 403


def test_create_month_rejects_a_squadron_in_the_body(client, admin_auth):
    """A caller must not be able to name the squadron it writes to."""
    response = client.post(
        "/roster/months",
        json={"month": MONTH, "squadronId": "other"},
        headers=admin_auth,
    )
    assert response.status_code == 422


def test_months_lists_newest_first(client, admin_auth):
    """The order the back and forward arrows depend on."""
    _create(client, admin_auth, "2027-01")
    _create(client, admin_auth, "2027-02")

    response = client.get("/roster/months", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["months"] == ["2027-02", "2027-01"]


def test_freeze_date_decides_frozen(client, admin_auth, seed):
    """Frozen is worked out from the freeze date, and never stored."""
    _create(client, admin_auth)
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    frozen = _freeze(client, admin_auth, today)
    assert frozen.status_code == 200
    assert frozen.json()["frozen"] is True

    thawed = _freeze(client, admin_auth, tomorrow)
    assert thawed.json()["frozen"] is False
    assert thawed.json()["freezeAt"] == tomorrow

    stored = (
        seed.collection(ROSTER_MONTHS_COLLECTION)
        .document(f"{STARS_ORG_UNIT_ID}:{MONTH}")
        .get()
        .to_dict()
    )
    assert "frozen" not in stored


@pytest.mark.usefixtures("seed")
def test_unknown_and_malformed_months_are_refused(client):
    """404 for a month that does not exist, 400 for one that cannot."""
    missing = client.get("/roster/months/2027-05", headers=HEADERS)
    assert missing.status_code == 404

    malformed = client.get("/roster/months/2027-13", headers=HEADERS)
    assert malformed.status_code == 400
    assert isinstance(malformed.json()["detail"], str)


def test_patch_unknown_month_is_404(client, admin_auth):
    """Both write paths refuse rather than quietly creating a month."""
    dates = client.patch(
        "/roster/months/2027-05/dates", json={"add": []}, headers=admin_auth
    )
    freeze = client.patch(
        "/roster/months/2027-05/freeze",
        json={"freezeAt": FREEZE_AT},
        headers=admin_auth,
    )
    assert dates.status_code == 404
    assert freeze.status_code == 404


def test_patch_dates_adds_removes_and_persists(client, admin_auth):
    """A midweek camp appears, a cancelled weekend goes, and it sticks."""
    _create(client, admin_auth)
    response = client.patch(
        f"/roster/months/{MONTH}/dates",
        json={"add": ["2027-01-21"], "remove": ["2027-01-03"]},
        headers=admin_auth,
    )
    assert response.status_code == 200

    expected = sorted((set(WEEKENDS) - {"2027-01-03"}) | {"2027-01-21"})
    assert response.json()["dates"] == expected

    again = client.get(f"/roster/months/{MONTH}", headers=HEADERS)
    assert again.json()["dates"] == expected


def test_patch_dates_outside_the_month_is_400(client, admin_auth):
    """A typo in another month must not land on the grid."""
    _create(client, admin_auth)
    response = client.patch(
        f"/roster/months/{MONTH}/dates",
        json={"add": ["2027-02-01"]},
        headers=admin_auth,
    )
    assert response.status_code == 400


def test_patch_dates_works_on_a_frozen_month(client, admin_auth):
    """Freezing stops availability changing, not the admin editing the grid."""
    _create(client, admin_auth)
    _freeze(client, admin_auth, date.today().isoformat())

    response = client.patch(
        f"/roster/months/{MONTH}/dates",
        json={"add": ["2027-01-21"]},
        headers=admin_auth,
    )
    assert response.status_code == 200
    assert response.json()["frozen"] is True
    assert "2027-01-21" in response.json()["dates"]
