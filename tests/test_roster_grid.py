"""Behavioural tests for the availability grid and setting a date."""

# Fixtures shadow their own names by design in pytest.
# pylint: disable=redefined-outer-name

import os
from datetime import date, timedelta

import pytest

from app.config import ROSTER_MONTHS_COLLECTION, STARS_ORG_UNIT_ID
from tests.conftest import (
    ADMIN_ID,
    ADMIN_NAME,
    HEADERS,
    MEMBER_ID,
    MEMBER_NAME,
)

pytestmark = pytest.mark.skipif(
    "FIRESTORE_EMULATOR_HOST" not in os.environ,
    reason="needs the Firestore emulator: gcloud emulators firestore start",
)

MONTH = "2027-03"
SATURDAY = "2027-03-06"
SUNDAY = "2027-03-07"
WEEKDAY = "2027-03-10"


def _open_month(store, freeze_at: date) -> None:
    """Put a month straight into Firestore, so no admin flow is needed first.

    Args:
        store: The synchronous Firestore client from ``seed``.
        freeze_at: The date the month freezes on.
    """
    store.collection(ROSTER_MONTHS_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:{MONTH}"
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "month": MONTH,
            "dates": [SATURDAY, SUNDAY],
            "freezeAt": freeze_at.isoformat(),
        }
    )


@pytest.fixture
def open_month(seed):
    """A month well clear of its freeze date, with two weekend dates on it."""
    _open_month(seed, date.today() + timedelta(days=30))
    return seed


@pytest.fixture
def frozen_month(seed):
    """The same month, already frozen."""
    _open_month(seed, date.today() - timedelta(days=1))
    return seed


def _set(client, auth, person_id: str, day: str, status: str, *, comment=None):
    """Set one person's answer for one date.

    Args:
        client: The test client.
        auth: The caller's Authorization header.
        person_id: Whose row to write.
        day: The flying date.
        status: Y, N or TBC.
        comment: Optional note shown on the grid.

    Returns:
        The HTTP response.
    """
    body = {"status": status}
    if comment is not None:
        body["comment"] = comment
    return client.put(
        f"/roster/months/{MONTH}/people/{person_id}/{day}", json=body, headers=auth
    )


def _row(client, person_id: str) -> dict:
    """Read one person's line off the grid.

    Args:
        client: The test client.
        person_id: Whose row to find.

    Returns:
        The row as the grid reports it.
    """
    response = client.get(f"/roster/months/{MONTH}/grid", headers=HEADERS)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    return next(row for row in rows if row["personId"] == person_id)


@pytest.mark.usefixtures("open_month")
def test_grid_lists_everyone_even_before_anyone_answers(client):
    """The grid is the whole squadron, not just the people who replied."""
    response = client.get(f"/roster/months/{MONTH}/grid", headers=HEADERS)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["dates"] == [SATURDAY, SUNDAY]
    assert body["frozen"] is False
    assert [row["name"] for row in body["rows"]] == [MEMBER_NAME, ADMIN_NAME]
    assert all(row["entries"] == {} for row in body["rows"])


def test_grid_needs_a_key(client):
    """The squadron's whereabouts are not readable without the dashboard key."""
    assert client.get(f"/roster/months/{MONTH}/grid").status_code == 401


@pytest.mark.usefixtures("seed")
def test_grid_for_a_month_that_was_never_opened_is_404(client):
    """Browsing past the earliest month must say so, not answer emptily."""
    assert client.get("/roster/months/2019-04/grid", headers=HEADERS).status_code == 404


@pytest.mark.usefixtures("open_month")
def test_setting_your_own_date_shows_up_on_the_grid(client, member_auth):
    """The whole point: you answer, and everybody can see it."""
    assert _set(
        client, member_auth, MEMBER_ID, SATURDAY, "N", comment="Work"
    ).json() == {"applied": True}

    row = _row(client, MEMBER_ID)
    assert row["entries"][SATURDAY]["status"] == "N"
    assert row["entries"][SATURDAY]["comment"] == "Work"
    assert row["entries"][SATURDAY]["updatedByName"] == MEMBER_NAME
    assert SUNDAY not in row["entries"]


@pytest.mark.usefixtures("open_month")
def test_answering_a_second_date_leaves_the_first_alone(client, member_auth):
    """Saving as you tap must not wipe what you already said."""
    _set(client, member_auth, MEMBER_ID, SATURDAY, "Y")
    _set(client, member_auth, MEMBER_ID, SUNDAY, "TBC")

    entries = _row(client, MEMBER_ID)["entries"]
    assert entries[SATURDAY]["status"] == "Y"
    assert entries[SUNDAY]["status"] == "TBC"


@pytest.mark.usefixtures("open_month")
def test_a_member_cannot_answer_for_someone_else(client, member_auth):
    """Nobody gets to volunteer a colleague for a Saturday."""
    assert _set(client, member_auth, ADMIN_ID, SATURDAY, "Y").status_code == 403


@pytest.mark.usefixtures("open_month")
def test_an_admin_answers_on_someones_behalf(client, admin_auth):
    """How the whole month gets filled in during phase one."""
    assert _set(client, admin_auth, MEMBER_ID, SATURDAY, "Y").status_code == 200

    entry = _row(client, MEMBER_ID)["entries"][SATURDAY]
    assert entry["status"] == "Y"
    assert entry["updatedBy"] == ADMIN_ID


@pytest.mark.usefixtures("open_month")
def test_a_date_that_is_not_flying_is_refused(client, member_auth):
    """A typed URL must not create a column nobody is flying on."""
    assert _set(client, member_auth, MEMBER_ID, WEEKDAY, "Y").status_code == 400


@pytest.mark.usefixtures("open_month")
def test_answering_for_someone_who_has_left_is_refused(client, admin_auth):
    """A stale name on the dashboard must not write a row nobody can see."""
    assert _set(client, admin_auth, "p-9999", SATURDAY, "Y").status_code == 404


@pytest.mark.usefixtures("open_month")
def test_setting_a_date_needs_a_session(client):
    """Reading is behind the squadron password, writing is behind a code."""
    response = client.put(
        f"/roster/months/{MONTH}/people/{MEMBER_ID}/{SATURDAY}", json={"status": "Y"}
    )
    assert response.status_code == 401


@pytest.mark.usefixtures("frozen_month")
def test_filling_a_blank_after_the_freeze_still_lands(client, member_auth):
    """New information, not a reversal, so the exec wants it straight away."""
    assert _set(client, member_auth, MEMBER_ID, SATURDAY, "Y").status_code == 200
    assert _row(client, MEMBER_ID)["entries"][SATURDAY]["status"] == "Y"


def test_changing_an_answer_after_the_freeze_is_refused(
    client, frozen_month, member_auth
):
    """The month is published, so an answer only moves with an admin's say-so."""
    frozen_month.collection("roster_availability").document(
        f"{STARS_ORG_UNIT_ID}:{MONTH}:{MEMBER_ID}"
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "month": MONTH,
            "personId": MEMBER_ID,
            "entries": {
                SATURDAY: {
                    "status": "Y",
                    "comment": None,
                    "updatedBy": MEMBER_ID,
                    "updatedByName": MEMBER_NAME,
                    "updatedAt": "2027-01-01T10:00:00+00:00",
                }
            },
        }
    )

    assert (
        _set(client, member_auth, MEMBER_ID, SATURDAY, "N", comment="Work").status_code
        == 403
    )
    assert _row(client, MEMBER_ID)["entries"][SATURDAY]["status"] == "Y"


@pytest.mark.usefixtures("frozen_month")
def test_an_admin_still_writes_after_the_freeze(client, admin_auth):
    """Somebody has to be able to fix the grid on the Friday night."""
    _set(client, admin_auth, MEMBER_ID, SATURDAY, "Y")

    assert _set(client, admin_auth, MEMBER_ID, SATURDAY, "N").status_code == 200
    assert _row(client, MEMBER_ID)["entries"][SATURDAY]["status"] == "N"


@pytest.mark.usefixtures("open_month")
def test_a_made_up_status_is_refused(client, member_auth):
    """Blank is the absence of an entry, never a fourth value."""
    assert _set(client, member_auth, MEMBER_ID, SATURDAY, "maybe").status_code == 422
