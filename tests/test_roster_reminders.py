"""Behavioural tests for the reminder dry run Cloud Scheduler calls each day."""

# pylint: disable=redefined-outer-name  # pytest fixtures shadow their own names

import os
from datetime import date, timedelta

import pytest

from app.config import (
    ROSTER_AVAILABILITY_COLLECTION,
    ROSTER_MONTHS_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.services import roster_months
from tests.conftest import ADMIN_ID, ADMIN_NAME, HEADERS, JOBS_KEY, MEMBER_ID

pytestmark = pytest.mark.skipif(
    "FIRESTORE_EMULATOR_HOST" not in os.environ,
    reason="needs the Firestore emulator: gcloud emulators firestore start",
)

MONTH = "2027-03"
DATES = ["2027-03-06", "2027-03-07"]
TODAY = date(2027, 2, 1)
JOBS = {"X-API-Key": JOBS_KEY}


@pytest.fixture
def freeze_in(seed, mocker):
    """Open a month freezing some days after TODAY, the admin answered, the member not.

    Returns:
        A function taking the days left until the freeze.
    """
    mocker.patch.object(roster_months, "uk_today", return_value=TODAY)
    # TBC is an answer, so the admin must never be reminded.
    entry = {
        "status": "TBC",
        "updatedBy": ADMIN_ID,
        "updatedByName": ADMIN_NAME,
        "updatedAt": "2027-01-01T00:00:00+00:00",
    }
    seed.collection(ROSTER_AVAILABILITY_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:{MONTH}:{ADMIN_ID}"
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "month": MONTH,
            "personId": ADMIN_ID,
            "entries": {day: entry for day in DATES},
            "pending": {},
        }
    )

    def _open(days_left: int) -> None:
        seed.collection(ROSTER_MONTHS_COLLECTION).document(
            f"{STARS_ORG_UNIT_ID}:{MONTH}"
        ).set(
            {
                "squadronId": STARS_ORG_UNIT_ID,
                "month": MONTH,
                "dates": DATES,
                "freezeAt": (TODAY + timedelta(days=days_left)).isoformat(),
            }
        )

    return _open


def _run(client) -> dict:
    """Call the job as Cloud Scheduler does.

    Args:
        client: The test client.

    Returns:
        The job's answer.
    """
    response = client.post("/roster/tasks/reminders", headers=JOBS)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_reminder_day_lists_only_people_with_blanks(client, freeze_in):
    """Six days out is a reminder day, and only the member left dates blank."""
    freeze_in(6)

    body = _run(client)

    assert body == {
        "dryRun": True,
        "reminders": [{"personId": MEMBER_ID, "month": MONTH, "blanks": 2}],
        "adminSummaries": [],
    }


def test_no_one_is_reminded_between_reminder_days(client, freeze_in):
    """Five days out is not a multiple of three, so nobody is listed."""
    freeze_in(5)

    assert _run(client)["reminders"] == []


def test_freeze_day_gives_the_admins_a_summary_and_nobody_a_reminder(client, freeze_in):
    """On the freeze date the job stops nagging and tells the admins who is missing."""
    freeze_in(0)

    body = _run(client)

    assert body["reminders"] == []
    assert body["adminSummaries"] == [{"month": MONTH, "personIds": [MEMBER_ID]}]


def test_a_month_already_frozen_is_left_alone(client, freeze_in):
    """The first run after deploy must not fire for old months."""
    freeze_in(-3)

    body = _run(client)

    assert body["reminders"] == []
    assert body["adminSummaries"] == []


@pytest.mark.usefixtures("seed")
def test_only_the_jobs_key_can_run_it(client):
    """The dashboard's read key must not reach a background job."""
    response = client.post("/roster/tasks/reminders", headers=HEADERS)

    assert response.status_code == 403
