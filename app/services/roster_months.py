"""Flying months for the roster: which dates they hold and when they freeze."""

import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.api_core.exceptions import Conflict
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from app.config import (
    ROSTER_FREEZE_DAYS,
    ROSTER_MONTHS_COLLECTION,
    ROSTER_TIMEZONE,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import RosterMonth
from app.services import database

logger = logging.getLogger(__name__)


def parse_month(month: str) -> date:
    """Turn a month into the date it starts on.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The first of that month.

    Raises:
        ValueError: If the month is not exactly YYYY-MM.
    """
    return date.fromisoformat(f"{month}-01")


def is_frozen(freeze_at: date) -> bool:
    """Say whether a month has frozen, against today in the UK.

    Args:
        freeze_at: The date the month freezes on.

    Returns:
        True from the freeze date onwards.
    """
    # Cloud Run runs in UTC, but the squadron and the freeze date are UK local.
    return datetime.now(ZoneInfo(ROSTER_TIMEZONE)).date() >= freeze_at


def owner_month(day: date) -> str:
    """Name the month whose grid a date sits on.

    Args:
        day: A flying date.

    Returns:
        Year and month, as 2026-10. A Sunday the 1st goes with its Saturday.
    """
    if day.day == 1 and day.weekday() == 6:
        day -= timedelta(days=1)
    return f"{day:%Y-%m}"


def outside_month(start: date, days: list[date]) -> list[date]:
    """Pick out the dates that do not belong to a month.

    Args:
        start: The first of the month.
        days: Dates the caller wants to add or remove.

    Returns:
        Those that sit on some other month's grid.
    """
    return [day for day in days if owner_month(day) != f"{start:%Y-%m}"]


def _key(month: str) -> str:
    """Build the document id for a month in the current squadron.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        Document id of the form ``{squadronId}:{month}``.
    """
    return f"{STARS_ORG_UNIT_ID}:{month}"


def _weekends(start: date) -> list[date]:
    """List every Saturday and Sunday on a month's grid.

    Args:
        start: The first of the month.

    Returns:
        The weekend dates, in order, with a Sunday the 1st after the month.
    """
    days = calendar.monthrange(start.year, start.month)[1]
    # One day past the end catches a Sunday the 1st of the next month.
    dates = [start + timedelta(days=offset) for offset in range(days + 1)]
    month = f"{start:%Y-%m}"
    return [d for d in dates if d.weekday() >= 5 and owner_month(d) == month]


def _live(data: dict | None) -> RosterMonth | None:
    """Read a stored month, treating a removed one as gone.

    Args:
        data: The document, or None if there is none.

    Returns:
        The month, or None if it is missing or removed.
    """
    if not data or data.get("removedAt"):
        return None
    return RosterMonth.model_validate(data)


async def create_month(month: str) -> RosterMonth:
    """Open a month with every weekend in it, or bring back a removed one as it was.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The new or restored month.

    Raises:
        google.api_core.exceptions.Conflict: If the month already exists.
    """
    start = parse_month(month)
    record = RosterMonth(
        squadron_id=STARS_ORG_UNIT_ID,
        month=month,
        dates=_weekends(start),
        freeze_at=start - timedelta(days=ROSTER_FREEZE_DAYS),
    )

    ref = database.get_collection(ROSTER_MONTHS_COLLECTION).document(_key(month))
    try:
        # create() fails if the document exists, so two admins cannot both win.
        await ref.create(record.model_dump(by_alias=True, mode="json"))
        return record
    except Conflict:
        data = (await ref.get()).to_dict() or {}
        if not data.get("removedAt"):
            raise

    logger.info("Month %s restored", month)
    await ref.update(
        {"removedBy": firestore.DELETE_FIELD, "removedAt": firestore.DELETE_FIELD}
    )
    return RosterMonth.model_validate(data)


async def get_month(month: str) -> RosterMonth | None:
    """Read one month.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The month, or None if it has not been created or was removed.
    """
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    return _live((await col.document(_key(month)).get()).to_dict())


async def list_months() -> list[str]:
    """List the squadron's months that have not been removed, newest first.

    Returns:
        Months as 2026-11, for the back and forward arrows.
    """
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    # ponytail: equality filter only, so the automatic single-field index serves it.
    query = col.where(filter=FieldFilter("squadronId", "==", STARS_ORG_UNIT_ID))
    months = [
        record.month async for doc in query.stream() if (record := _live(doc.to_dict()))
    ]
    return sorted(months, reverse=True)


async def get_months(months: set[str]) -> list[RosterMonth]:
    """Read several months in one round trip.

    Args:
        months: Months as 2026-11.

    Returns:
        Those that exist and were not removed, in no particular order.
    """
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    refs = [col.document(_key(month)) for month in months]
    if not refs:
        return []

    return [
        record
        async for snapshot in database.get_client().get_all(refs)
        if (record := _live(snapshot.to_dict()))
    ]


async def update_dates(ref, add: list[date], remove: list[date]) -> dict | None:
    """Add and remove dates on a month or course, leaving any answers in place.

    A date in both lists ends up on the grid, since removals are applied first.

    Args:
        ref: The month or course document.
        add: Dates to put on the grid.
        remove: Dates to take off it.

    Returns:
        The updated document, or None if it does not exist or was removed.
    """
    # ISO strings are what the document holds, and they sort chronologically.
    additions = {day.isoformat() for day in add}
    removals = {day.isoformat() for day in remove}

    @firestore.async_transactional
    async def apply(transaction) -> dict | None:
        """Rewrite the whole list at once, so two admins cannot lose an edit."""
        snapshot = await ref.get(transaction=transaction)
        data = snapshot.to_dict() or {}
        if not snapshot.exists or data.get("removedAt"):
            return None

        dates = sorted((set(data.get("dates") or []) - removals) | additions)
        transaction.update(ref, {"dates": dates})
        return {**data, "dates": dates}

    # Raising in here would fire again on every retry, so the 404 is raised outside.
    return await apply(database.get_client().transaction())


async def patch_dates(
    month: str, add: list[date], remove: list[date]
) -> RosterMonth | None:
    """Add and remove flying dates, leaving any answers in place.

    Args:
        month: Year and month, as 2026-11.
        add: Dates to put on the grid.
        remove: Dates to take off it.

    Returns:
        The updated month, or None if there is no such month.
    """
    ref = database.get_collection(ROSTER_MONTHS_COLLECTION).document(_key(month))
    record = await update_dates(ref, add, remove)
    if record is None:
        return None
    return RosterMonth.model_validate(record)


async def set_freeze(month: str, freeze_at: date) -> RosterMonth | None:
    """Move the date a month freezes on.

    Args:
        month: Year and month, as 2026-11.
        freeze_at: The new freeze date.

    Returns:
        The updated month, or None if there is no such month.
    """
    if await get_month(month) is None:
        return None
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    await col.document(_key(month)).update({"freezeAt": freeze_at.isoformat()})
    return await get_month(month)


async def remove_month(month: str, removed_by: str) -> bool:
    """Hide a month, keeping it and every answer and note on its dates.

    Args:
        month: Year and month, as 2026-11.
        removed_by: The admin removing it.

    Returns:
        False if there was no such month to remove.
    """
    if await get_month(month) is None:
        return False
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    await col.document(_key(month)).update(
        {"removedBy": removed_by, "removedAt": datetime.now(UTC).isoformat()}
    )
    return True
