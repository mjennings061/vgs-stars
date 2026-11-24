"""Tests for reading the squadron from STARS and caching it in Firestore."""

# Fixtures shadow their own names by design in pytest.
# pylint: disable=redefined-outer-name

import os
from datetime import UTC, datetime, timedelta

import pytest
import requests

from app.config import ROSTER_PEOPLE_COLLECTION, STARS_ORG_UNIT_ID
from app.models.roster import Role
from app.models.stars import Person, User
from app.services import stars_client
from scripts.roster_people import seed_people
from tests.conftest import deactivate

needs_emulator = pytest.mark.skipif(
    "FIRESTORE_EMULATOR_HOST" not in os.environ,
    reason="needs the Firestore emulator: gcloud emulators firestore start",
)


def _record(person_id: str, name: str, **extra) -> dict:
    """Build a STARS person payload with every field STARS usually sends.

    Args:
        person_id: STARS person identifier.
        name: Their name.
        **extra: Fields to add or override, such as endDate.

    Returns:
        The payload as STARS would return it.
    """
    return {
        "id": person_id,
        "name": name,
        "userId": f"u-{person_id}",
        "resourceTypeId": "rt-1",
        "resourceType": "Person",
        "orgUnitId": 661,
        "orgUnit": "661 VGS",
        **extra,
    }


@pytest.fixture
def stars(mocker):
    """Answer both STARS calls in get_people_for_unit from a canned payload."""

    def _install(records: list[dict]):
        """Return the ids listing first, then the detail for those ids."""
        listing = mocker.Mock()
        listing.json.return_value = {"data": [{"id": r["id"]} for r in records]}
        detail = mocker.Mock()
        detail.json.return_value = {"data": records}
        return mocker.patch.object(requests, "get", side_effect=[listing, detail])

    return _install


def test_unusable_record_is_skipped_not_fatal(stars):
    """One person with no STARS account must not cost the whole squadron."""
    stars([_record("p-1", "J Doe"), {"id": "p-2", "name": "No Account"}])

    people = stars_client.get_people_for_unit(STARS_ORG_UNIT_ID)

    assert [person.id for person in people] == ["p-1"]


def test_ended_posting_is_not_on_the_unit(stars):
    """Someone STARS still lists but who has left is filtered out."""
    gone = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    still_here = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    stars(
        [
            _record("p-1", "J Doe"),
            _record("p-2", "A Leaver", endDate=gone),
            _record("p-3", "F Uture", endDate=still_here),
        ]
    )

    people = stars_client.get_people_for_unit(STARS_ORG_UNIT_ID)

    assert [person.id for person in people] == ["p-1", "p-3"]


def test_malformed_response_is_a_stars_error(mocker):
    """A 200 carrying junk must surface like every other STARS failure."""
    response = mocker.Mock()
    response.json.side_effect = ValueError("not json")
    mocker.patch.object(requests, "get", return_value=response)

    with pytest.raises(stars_client.StarsAPIError):
        stars_client.get_people_for_unit(STARS_ORG_UNIT_ID)


def _stars_lists(mocker, *person_ids: str) -> None:
    """Have STARS list exactly these people, each with an account and email.

    Args:
        mocker: The pytest-mock fixture.
        *person_ids: Who STARS says is on the unit.
    """
    mocker.patch.object(
        stars_client,
        "get_people_for_unit",
        return_value=[Person(**_record(pid, pid)) for pid in person_ids],
    )
    mocker.patch.object(
        stars_client,
        "get_users",
        return_value=[
            User(
                id=f"u-{pid}",
                name=pid,
                email=f"{pid}@example.com",
                status="active",
                baseStatus="active",
            )
            for pid in person_ids
        ],
    )


@needs_emulator
def test_sync_marks_leavers_inactive_and_keeps_roles(seed, mocker):
    """A rerun keeps whoever left as inactive, without disturbing the admins."""
    _stars_lists(mocker, "p-1043")

    seed_people()

    people = seed.collection(ROSTER_PEOPLE_COLLECTION)
    # p-2210 was seeded by the fixture and STARS no longer lists them.
    leaver = people.document(f"{STARS_ORG_UNIT_ID}:p-2210").get().to_dict()
    assert leaver["isActive"] is False
    kept = people.document(f"{STARS_ORG_UNIT_ID}:p-1043").get().to_dict()
    assert kept["isActive"] is True
    assert kept["role"] == Role.ADMIN.value


@needs_emulator
def test_a_leaver_loses_their_email_and_admin_role(seed, mocker):
    """Nothing is kept that could email them or let them back in as an admin."""
    _stars_lists(mocker, "p-2210")

    seed_people()

    leaver = seed.collection(ROSTER_PEOPLE_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:p-1043"
    )
    record = leaver.get().to_dict()
    assert record["isActive"] is False
    assert record["role"] == Role.MEMBER.value
    assert "email" not in record


@needs_emulator
def test_sync_brings_back_someone_who_rejoins(seed, mocker):
    """Back on the unit in STARS means back on the roster."""
    deactivate(seed, "p-2210")
    _stars_lists(mocker, "p-1043", "p-2210")

    seed_people()

    member = seed.collection(ROSTER_PEOPLE_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:p-2210"
    )
    assert member.get().to_dict()["isActive"] is True


@needs_emulator
def test_sync_refuses_to_wipe_on_an_empty_stars_answer(seed, mocker):
    """A bad STARS response must not delete the squadron and its admins."""
    mocker.patch.object(stars_client, "get_people_for_unit", return_value=[])

    seed_people()

    people = seed.collection(ROSTER_PEOPLE_COLLECTION)
    assert people.document(f"{STARS_ORG_UNIT_ID}:p-1043").get().exists
    assert people.document(f"{STARS_ORG_UNIT_ID}:p-2210").get().exists
