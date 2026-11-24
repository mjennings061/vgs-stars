"""Daily roster jobs that Cloud Scheduler calls with the jobs key."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.config import SCOPE_ROSTER_JOBS
from app.security import require_scope
from app.services import roster_months, roster_reminders

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/roster/tasks",
    tags=["roster"],
    dependencies=[Depends(require_scope(SCOPE_ROSTER_JOBS))],
)


class ReminderResponse(BaseModel):
    """One person who would be reminded today."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    person_id: str
    month: str
    blanks: int


class AdminSummaryResponse(BaseModel):
    """A month freezing today, and who still has blank dates on it."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    month: str
    person_ids: list[str]


class RemindersResponse(BaseModel):
    """What the reminder job decided today."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    dry_run: bool = True
    reminders: list[ReminderResponse]
    admin_summaries: list[AdminSummaryResponse]


@router.post("/reminders", response_model=RemindersResponse)
async def plan_reminders() -> RemindersResponse:
    """Log who would be reminded today, sending nothing.

    Returns:
        The reminders and admin summaries due today.
    """
    reminders, summaries = await roster_reminders.plan(roster_months.uk_today())
    for reminder in reminders:
        logger.info(
            "Reminder due (dry run): person %s, month %s, %d blank dates",
            reminder.person_id,
            reminder.month,
            reminder.blanks,
        )
    for summary in summaries:
        logger.info(
            "Admin summary due (dry run): month %s, %d with blanks: %s",
            summary.month,
            len(summary.person_ids),
            ", ".join(summary.person_ids),
        )

    return RemindersResponse(
        reminders=[ReminderResponse.model_validate(r) for r in reminders],
        admin_summaries=[AdminSummaryResponse.model_validate(s) for s in summaries],
    )
