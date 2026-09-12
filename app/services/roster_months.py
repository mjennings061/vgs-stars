"""Flying months for the roster: which dates they hold and when they freeze."""

import calendar
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.api_core.exceptions import NotFound
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


def outside_month(start: date, days: list[date]) -> list[date]:
    """Pick out the dates that do not belong to a month.

    Args:
        start: The first of the month.
        days: Dates the caller wants to add or remove.

    Returns:
        Those that fall in some other month.
    """
    return [day for day in days if (day.year, day.month) != (start.year, start.month)]


def _key(month: str) -> str:
    """Build the document id for a month in the current squadron.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        Document id of the form ``{squadronId}:{month}``.
    """
    return f"{STARS_ORG_UNIT_ID}:{month}"


def _weekends(start: date) -> list[date]:
    """List every Saturday and Sunday in a month.

    Args:
        start: The first of the month.

    Returns:
        The weekend dates, in order.
    """
    days = calendar.monthrange(start.year, start.month)[1]
    dates = [start.replace(day=day) for day in range(1, days + 1)]
    return [day for day in dates if day.weekday() >= 5]


async def create_month(month: str) -> RosterMonth:
    """Open a month with every weekend in it and a freeze date two weeks before.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The new month.

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

    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    # create() fails if the document exists, so two admins cannot both win.
    await col.document(_key(month)).create(
        record.model_dump(by_alias=True, mode="json")
    )
    return record


async def get_month(month: str) -> RosterMonth | None:
    """Read one month.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The month, or None if it has not been created.
    """
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    snapshot = await col.document(_key(month)).get()
    if not snapshot.exists:
        return None
    return RosterMonth.model_validate(snapshot.to_dict())


async def list_months() -> list[str]:
    """List the squadron's months, newest first.

    Returns:
        Months as 2026-11, for the back and forward arrows.
    """
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    # ponytail: equality filter only, so the automatic single-field index serves it.
    query = col.where(filter=FieldFilter("squadronId", "==", STARS_ORG_UNIT_ID))
    months = [(doc.to_dict() or {}).get("month") async for doc in query.stream()]
    return sorted((month for month in months if month), reverse=True)


async def patch_dates(
    month: str, add: list[date], remove: list[date]
) -> RosterMonth | None:
    """Add and remove flying dates, leaving any answers in place.

    A date in both lists ends up on the grid, since removals are applied first.

    Args:
        month: Year and month, as 2026-11.
        add: Dates to put on the grid.
        remove: Dates to take off it.

    Returns:
        The updated month, or None if there is no such month.
    """
    client = database.get_client()
    ref = database.get_collection(ROSTER_MONTHS_COLLECTION).document(_key(month))
    # ISO strings are what the document holds, and they sort chronologically.
    additions = {day.isoformat() for day in add}
    removals = {day.isoformat() for day in remove}

    @firestore.async_transactional
    async def apply(transaction) -> dict | None:
        """Rewrite the whole list at once, so two admins cannot lose an edit."""
        snapshot = await ref.get(transaction=transaction)
        if not snapshot.exists:
            return None

        data = snapshot.to_dict() or {}
        dates = sorted((set(data.get("dates", [])) - removals) | additions)
        transaction.update(ref, {"dates": dates})
        return {**data, "dates": dates}

    # Raising in here would fire again on every retry, so the 404 is raised outside.
    record = await apply(client.transaction())
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
    col = database.get_collection(ROSTER_MONTHS_COLLECTION)
    try:
        await col.document(_key(month)).update({"freezeAt": freeze_at.isoformat()})
    except NotFound:
        return None
    return await get_month(month)
