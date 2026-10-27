"""Day notes for the roster: the boxes admins fill in above each flying date."""

from datetime import UTC, date, datetime

from google.cloud import firestore

from app.config import (
    ROSTER_DAY_EDITS_COLLECTION,
    ROSTER_DAYS_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import RosterDay
from app.services import database


def _ref(day: date):
    """Point at one date's notes.

    Args:
        day: The flying date.

    Returns:
        The document reference, keyed ``{squadronId}:{date}``.
    """
    return database.get_collection(ROSTER_DAYS_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:{day.isoformat()}"
    )


async def save(
    day: date, changes: dict[str, str | None], updated_by: str, updated_by_name: str
) -> None:
    """Write the boxes that changed for one date, and log the edit beside them.

    A save that changes nothing writes nothing, so it moves no "updated by".

    Args:
        day: The flying date.
        changes: Only the boxes being set, an empty string or None clearing one.
        updated_by: The admin doing it.
        updated_by_name: Their name.
    """
    ref = _ref(day)
    edits = database.get_collection(ROSTER_DAY_EDITS_COLLECTION)

    @firestore.async_transactional
    async def write(transaction) -> None:
        """Compare with the stored boxes and write only what differs."""
        stored = (await ref.get(transaction=transaction)).to_dict() or {}
        # An empty string and None both mean an empty box.
        changed = {
            box: text
            for box, text in changes.items()
            if (text or "") != (stored.get(box) or "")
        }
        if not changed:
            return

        stamp = {
            "squadronId": STARS_ORG_UNIT_ID,
            "date": day.isoformat(),
            "updatedBy": updated_by,
            "updatedByName": updated_by_name,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        # Merging writes only these boxes, so two admins cannot wipe each other's.
        transaction.set(ref, {**changed, **stamp}, merge=True)
        # Appended, never updated, so a cleared note stays on the record.
        transaction.set(edits.document(), {**stamp, "changes": changed})

    await write(database.get_client().transaction())


async def read_days(dates: list[date]) -> dict[str, RosterDay]:
    """Read the notes for a grid's dates in one round trip.

    Args:
        dates: The grid's dates.

    Returns:
        The notes keyed by ISO date, missing any date nobody has written on.
    """
    refs = [_ref(day) for day in dates]
    if not refs:
        return {}

    records = [
        RosterDay.model_validate(snapshot.to_dict())
        async for snapshot in database.get_client().get_all(refs)
        if snapshot.exists
    ]
    return {record.date.isoformat(): record for record in records}
