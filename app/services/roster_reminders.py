"""Who is due a reminder to fill in their dates, and which admin summaries are due.

Nothing is sent yet. The emails come with issue #26; until then this is a dry run.
"""

import logging
from dataclasses import dataclass
from datetime import date

from app.config import ROSTER_REMINDER_EVERY_DAYS
from app.services import roster_auth, roster_availability, roster_months

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reminder:
    """A person with blank dates on a month that has not frozen yet."""

    person_id: str
    month: str
    blanks: int


@dataclass(frozen=True)
class AdminSummary:
    """Everyone who still had a blank date on the day a month froze."""

    month: str
    person_ids: list[str]


def is_due(today: date, freeze_at: date) -> bool:
    """Say whether today is a reminder day for a month.

    Args:
        today: Today in the UK.
        freeze_at: The date the month freezes on.

    Returns:
        True before the freeze, on days whose count to it divides evenly.
    """
    days_left = (freeze_at - today).days
    return days_left > 0 and days_left % ROSTER_REMINDER_EVERY_DAYS == 0


async def plan(today: date) -> tuple[list[Reminder], list[AdminSummary]]:
    """Work out today's reminders and admin summaries, sending nothing.

    Args:
        today: Today in the UK.

    Returns:
        The reminders due, and a summary for each month freezing today.
    """
    months = await roster_months.get_months(set(await roster_months.list_months()))
    due = [m for m in months if m.freeze_at == today or is_due(today, m.freeze_at)]
    if not due:
        return [], []

    people = await roster_auth.list_people()
    reminders: list[Reminder] = []
    summaries: list[AdminSummary] = []
    for month in sorted(due, key=lambda m: m.month):
        answers = await roster_availability.read_dates(
            month.dates, [person.person_id for person in people]
        )
        wanted = {day.isoformat() for day in month.dates}
        # Any answer counts, TBC included; only a date with no entry is blank.
        blanks = {
            person.person_id: len(
                wanted - answers.get(person.person_id, ({}, {}))[0].keys()
            )
            for person in people
        }
        missing = [person for person in people if blanks[person.person_id]]

        if month.freeze_at == today:
            summaries.append(AdminSummary(month.month, [p.person_id for p in missing]))
            continue
        for person in missing:
            if not person.email:
                logger.info(
                    "Reminder due but no email: person %s, month %s",
                    person.person_id,
                    month.month,
                )
                continue
            reminders.append(
                Reminder(person.person_id, month.month, blanks[person.person_id])
            )
    return reminders, summaries
