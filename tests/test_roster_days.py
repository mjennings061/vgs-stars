"""Behavioural tests for the note boxes above each date."""

# pylint: disable=redefined-outer-name

import os
from datetime import date, timedelta

import pytest

from app.config import (
    ROSTER_DAY_EDITS_COLLECTION,
    ROSTER_MONTHS_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from tests.conftest import ADMIN_NAME, HEADERS

pytestmark = pytest.mark.skipif(
    "FIRESTORE_EMULATOR_HOST" not in os.environ,
    reason="needs the Firestore emulator: gcloud emulators firestore start",
)

MONTH = "2027-03"
SATURDAY = "2027-03-06"


@pytest.fixture
def open_month(seed):
    """A month with one Saturday on it, well clear of its freeze date."""
    seed.collection(ROSTER_MONTHS_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:{MONTH}"
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "month": MONTH,
            "dates": [SATURDAY],
            "freezeAt": (date.today() + timedelta(days=30)).isoformat(),
        }
    )
    return seed


def _patch(client, auth: dict, day: str, body: dict):
    """Set note boxes as the dashboard does, with the key and a session.

    Args:
        client: The test client.
        auth: The caller's Authorization header.
        day: The date.
        body: The boxes to set.

    Returns:
        The HTTP response.
    """
    return client.patch(f"/roster/days/{day}", json=body, headers={**HEADERS, **auth})


def test_a_note_is_set_then_cleared_and_both_edits_are_kept(
    client, open_month, admin_auth
):
    """The grid shows the latest notes; the history keeps every edit."""
    assert _patch(client, admin_auth, SATURDAY, {"event": "QVS"}).status_code == 200
    assert _patch(client, admin_auth, SATURDAY, {"gs": "MW LJ"}).status_code == 200

    grid = client.get(f"/roster/months/{MONTH}/grid", headers=HEADERS).json()
    note = grid["days"][SATURDAY]
    assert (note["event"], note["gs"], note["updatedByName"]) == (
        "QVS",
        "MW LJ",
        ADMIN_NAME,
    )

    assert _patch(client, admin_auth, SATURDAY, {"event": ""}).status_code == 200
    grid = client.get(f"/roster/months/{MONTH}/grid", headers=HEADERS).json()
    assert (grid["days"][SATURDAY]["event"], grid["days"][SATURDAY]["gs"]) == (
        "",
        "MW LJ",
    )

    # Saving what is already there changes nothing, so leaves no edit behind.
    for body in ({"gs": "MW LJ"}, {"event": None}):
        assert _patch(client, admin_auth, SATURDAY, body).status_code == 200

    edits = [
        doc.to_dict()["changes"]
        for doc in open_month.collection(ROSTER_DAY_EDITS_COLLECTION).stream()
    ]
    assert sorted(edits, key=str) == sorted(
        [{"event": "QVS"}, {"gs": "MW LJ"}, {"event": ""}], key=str
    )


@pytest.mark.usefixtures("open_month")
def test_notes_are_refused_off_grid_too_long_empty_or_from_a_member(
    client, admin_auth, member_auth
):
    """Only an admin may write, only on a grid's date, within the limits."""
    assert _patch(client, admin_auth, "2027-03-10", {"event": "x"}).status_code == 400
    assert _patch(client, admin_auth, SATURDAY, {"gs": "x" * 201}).status_code == 422
    assert _patch(client, admin_auth, SATURDAY, {}).status_code == 422
    assert _patch(client, admin_auth, SATURDAY, {"who": "x"}).status_code == 422
    assert _patch(client, member_auth, SATURDAY, {"event": "x"}).status_code == 403
    response = client.patch(f"/roster/days/{SATURDAY}", json={"event": "x"})
    assert response.status_code == 401
