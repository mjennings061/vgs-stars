"""Roster course endpoints: named grids whose dates can cross a month end."""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config import SCOPE_ROSTER_READ
from app.models.roster import RosterCourse
from app.routes.roster import (
    MAX_GRID_DATES,
    GridResponse,
    PatchDatesRequest,
    PatchFreezeRequest,
    check_freeze,
    grid_rows,
)
from app.security import require_admin, require_scope
from app.services import roster_courses, roster_months

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roster", tags=["roster"])


def _unknown_course() -> HTTPException:
    """Build the 404 every course lookup shares.

    Returns:
        The 404 to raise.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown course")


class CourseSummary(BaseModel):
    """One course, as the arrows between grids need it."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    course_id: str
    title: str
    first_date: date
    last_date: date


class CoursesResponse(BaseModel):
    """The courses that exist, by first date."""

    courses: list[CourseSummary]


class CourseResponse(BaseModel):
    """A course's dates, its freeze date, and whether it has frozen."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    course_id: str
    title: str
    dates: list[date]
    freeze_at: date
    frozen: bool

    @classmethod
    def of(cls, record: RosterCourse) -> "CourseResponse":
        """Answer with a stored course, working out whether it has frozen.

        Args:
            record: The course as Firestore holds it.

        Returns:
            The response body.
        """
        return cls(
            course_id=record.course_id,
            title=record.title,
            dates=record.dates,
            freeze_at=record.freeze_at,
            frozen=roster_months.is_frozen(record.freeze_at),
        )


class CreateCourseRequest(BaseModel):
    """Body naming a course and every date on it."""

    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", str_strip_whitespace=True
    )

    title: str = Field(..., min_length=1, max_length=60)
    dates: list[date] = Field(default_factory=list, max_length=MAX_GRID_DATES)


@router.get(
    "/courses",
    response_model=CoursesResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_courses() -> CoursesResponse:
    """List the courses the arrows can move between.

    Returns:
        Each course's id, title, first and last date.
    """
    return CoursesResponse(
        courses=[
            CourseSummary(
                course_id=course.course_id,
                title=course.title,
                first_date=course.dates[0],
                last_date=course.dates[-1],
            )
            for course in await roster_courses.list_courses()
        ]
    )


@router.post(
    "/courses",
    response_model=CourseResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def create_course(
    request: CreateCourseRequest, session: dict = Depends(require_admin)
) -> CourseResponse:
    """Open a course, freezing two weeks before its first day.

    Args:
        request: The course's title and dates.
        session: The admin doing it.

    Returns:
        The new course.

    Raises:
        HTTPException: 400 if it has no dates.
    """
    if not request.dates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A course needs at least one date",
        )

    record = await roster_courses.create_course(request.title, request.dates)
    logger.info("Course %s created by %s", record.course_id, session["personId"])
    return CourseResponse.of(record)


@router.get(
    "/courses/{course_id}",
    response_model=CourseResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_course(course_id: str) -> CourseResponse:
    """Read one course's dates and freeze state.

    Args:
        course_id: The course's id.

    Returns:
        The course, with ``frozen`` worked out from today.

    Raises:
        HTTPException: 404 if it does not exist or was removed.
    """
    record = await roster_courses.get_course(course_id)
    if record is None:
        raise _unknown_course()
    return CourseResponse.of(record)


@router.patch(
    "/courses/{course_id}/dates",
    response_model=CourseResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def patch_course_dates(
    course_id: str, request: PatchDatesRequest, session: dict = Depends(require_admin)
) -> CourseResponse:
    """Add or remove a course's dates, frozen or not.

    Args:
        course_id: The course's id.
        request: The dates to add and remove.
        session: The admin doing it.

    Returns:
        The updated course.

    Raises:
        HTTPException: 400 if no dates would be left, 404 if no such course.
    """
    record = await roster_courses.get_course(course_id)
    if record is None:
        raise _unknown_course()
    if not (set(record.dates) - set(request.remove)) | set(request.add):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A course needs at least one date; remove the course instead",
        )

    record = await roster_courses.patch_dates(course_id, request.add, request.remove)
    if record is None:
        raise _unknown_course()

    logger.info("Course %s dates changed by %s", course_id, session["personId"])
    return CourseResponse.of(record)


@router.patch(
    "/courses/{course_id}/freeze",
    response_model=CourseResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def patch_course_freeze(
    course_id: str, request: PatchFreezeRequest, session: dict = Depends(require_admin)
) -> CourseResponse:
    """Move the date a course freezes on.

    Args:
        course_id: The course's id.
        request: The new freeze date.
        session: The admin doing it.

    Returns:
        The updated course.

    Raises:
        HTTPException: 400 for a wild freeze date, 404 if no such course.
    """
    record = await roster_courses.get_course(course_id)
    if record is None:
        raise _unknown_course()
    check_freeze(request.freeze_at, record.dates[0], record.title)

    record = await roster_courses.set_freeze(course_id, request.freeze_at)
    if record is None:
        raise _unknown_course()

    logger.info("Course %s freeze moved by %s", course_id, session["personId"])
    return CourseResponse.of(record)


@router.delete(
    "/courses/{course_id}",
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def remove_course(course_id: str, session: dict = Depends(require_admin)) -> dict:
    """Hide a course; its answers stay, and show again on any grid with its dates.

    Args:
        course_id: The course's id.
        session: The admin doing it.

    Returns:
        An empty acknowledgement.

    Raises:
        HTTPException: 404 if there is no such course.
    """
    if not await roster_courses.remove_course(course_id, session["personId"]):
        raise _unknown_course()

    logger.info("Course %s removed by %s", course_id, session["personId"])
    return {}


@router.get(
    "/courses/{course_id}/grid",
    response_model=GridResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_course_grid(course_id: str) -> GridResponse:
    """Draw a whole course for the whole squadron, across any month end.

    Args:
        course_id: The course's id.

    Returns:
        Every person as a row, with only the dates they have answered.

    Raises:
        HTTPException: 404 if it does not exist or was removed.
    """
    record = await roster_courses.get_course(course_id)
    if record is None:
        raise _unknown_course()

    rows, frozen_dates = await grid_rows(record.dates, record.freeze_at)
    return GridResponse(
        course_id=record.course_id,
        title=record.title,
        dates=record.dates,
        freeze_at=record.freeze_at,
        frozen=roster_months.is_frozen(record.freeze_at),
        frozen_dates=frozen_dates,
        rows=rows,
    )
