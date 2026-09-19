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


@needs_emulator
def test_sync_removes_leavers_and_keeps_roles(seed, mocker):
    """A rerun drops whoever left, without disturbing the admins who stayed."""
    stayed = Person(**_record("p-1043", "J Doe"))
    mocker.patch.object(stars_client, "get_people_for_unit", return_value=[stayed])
    mocker.patch.object(
        stars_client,
        "get_users",
        return_value=[
            User(
                id="u-p-1043",
                name="J Doe",
                email="jdoe@example.com",
                status="active",
                baseStatus="active",
            )
        ],
    )

    seed_people()

    people = seed.collection(ROSTER_PEOPLE_COLLECTION)
    # p-2210 was seeded by the fixture and STARS no longer lists them.
    assert not people.document(f"{STARS_ORG_UNIT_ID}:p-2210").get().exists
    kept = people.document(f"{STARS_ORG_UNIT_ID}:p-1043").get()
    assert kept.exists
    assert kept.to_dict()["role"] == Role.ADMIN.value


@needs_emulator
def test_sync_refuses_to_wipe_on_an_empty_stars_answer(seed, mocker):
    """A bad STARS response must not delete the squadron and its admins."""
    mocker.patch.object(stars_client, "get_people_for_unit", return_value=[])

    seed_people()

    people = seed.collection(ROSTER_PEOPLE_COLLECTION)
    assert people.document(f"{STARS_ORG_UNIT_ID}:p-1043").get().exists
    assert people.document(f"{STARS_ORG_UNIT_ID}:p-2210").get().exists
