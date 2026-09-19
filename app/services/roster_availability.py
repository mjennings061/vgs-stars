"""Who said what about each flying date, one document per person per month."""

import logging
from urllib.parse import quote

from app.config import ROSTER_AVAILABILITY_COLLECTION, STARS_ORG_UNIT_ID
from app.models.roster import RosterAvailability, RosterEntry
from app.services import database

logger = logging.getLogger(__name__)


def _key(month: str, person_id: str) -> str:
    """Build the document id for one person's month.

    Args:
        month: Year and month, as 2026-11.
        person_id: STARS person identifier.

    Returns:
        Document id of the form ``{squadronId}:{month}:{personId}``.
    """
    # Quoting keeps an id like "a/b" one path segment instead of a 500.
    return f"{STARS_ORG_UNIT_ID}:{month}:{quote(person_id, safe='')}"


async def read_month(
    month: str, person_ids: list[str]
) -> dict[str, dict[str, RosterEntry]]:
    """Read a whole month of answers in one round trip.

    Args:
        month: Year and month, as 2026-11.
        person_ids: Everyone whose row the grid needs.

    Returns:
        Entries by person id and then by date, missing anyone who has
        answered nothing.
    """
    if not person_ids:
        return {}

    col = database.get_collection(ROSTER_AVAILABILITY_COLLECTION)
    # Fetching by id needs no composite index, which the emulator cannot enforce.
    refs = [col.document(_key(month, person_id)) for person_id in person_ids]

    rows = {}
    async for snapshot in database.get_client().get_all(refs):
        if snapshot.exists:
            record = RosterAvailability.model_validate(snapshot.to_dict())
            rows[record.person_id] = record.entries
    return rows


async def set_entry(month: str, person_id: str, day: str, entry: RosterEntry) -> None:
    """Write one person's answer for one date.

    Args:
        month: Year and month, as 2026-11.
        person_id: Whose row is being written.
        day: The date, as 2026-11-08.
        entry: The answer and who set it.
    """
    record = RosterAvailability(
        squadron_id=STARS_ORG_UNIT_ID,
        month=month,
        person_id=person_id,
        entries={day: entry},
    )

    col = database.get_collection(ROSTER_AVAILABILITY_COLLECTION)
    # Merging writes just this date, so saving two dates at once cannot clash.
    await col.document(_key(month, person_id)).set(
        record.model_dump(by_alias=True, mode="json"), merge=True
    )
    logger.info("Availability set for person %s on %s", person_id, day)
