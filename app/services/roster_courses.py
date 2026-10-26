"""Courses for the roster: named runs of flying dates that can cross a month end."""

from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

from google.cloud.firestore_v1 import FieldFilter

from app.config import ROSTER_COURSES_COLLECTION, ROSTER_FREEZE_DAYS, STARS_ORG_UNIT_ID
from app.models.roster import RosterCourse
from app.services import database, roster_months


def _ref(course_id: str):
    """Point at one course's document.

    Args:
        course_id: The course's id.

    Returns:
        The document reference, keyed ``{squadronId}:{courseId}``.
    """
    # Quoting keeps an id like "a/b" one path segment instead of a 500.
    return database.get_collection(ROSTER_COURSES_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:{quote(course_id, safe='')}"
    )


def _live(data: dict | None) -> RosterCourse | None:
    """Read a stored course, treating a removed or emptied one as gone.

    Args:
        data: The document, or None if there is none.

    Returns:
        The course, or None if it is missing, removed or has no dates left.
    """
    # Two admins trimming at once can leave no dates, and the list cannot draw that.
    if not data or data.get("removedAt") or not data.get("dates"):
        return None
    return RosterCourse.model_validate(data)


async def create_course(title: str, dates: list[date]) -> RosterCourse:
    """Open a course that freezes two weeks before its first day.

    Args:
        title: What the course is called.
        dates: Every flying date on it, at least one.

    Returns:
        The new course.
    """
    course_id = database.get_collection(ROSTER_COURSES_COLLECTION).document().id
    days = sorted(set(dates))
    record = RosterCourse(
        squadron_id=STARS_ORG_UNIT_ID,
        course_id=course_id,
        title=title,
        dates=days,
        freeze_at=days[0] - timedelta(days=ROSTER_FREEZE_DAYS),
    )
    await _ref(course_id).create(record.model_dump(by_alias=True, mode="json"))
    return record


async def get_course(course_id: str) -> RosterCourse | None:
    """Read one course.

    Args:
        course_id: The course's id.

    Returns:
        The course, or None if it does not exist or was removed.
    """
    return _live((await _ref(course_id).get()).to_dict())


async def list_courses() -> list[RosterCourse]:
    """List the squadron's courses that have not been removed.

    Returns:
        The courses, by first date.
    """
    query = database.get_collection(ROSTER_COURSES_COLLECTION).where(
        filter=FieldFilter("squadronId", "==", STARS_ORG_UNIT_ID)
    )
    # Reads every course; filter by last date if there are ever hundreds.
    courses = [
        course async for doc in query.stream() if (course := _live(doc.to_dict()))
    ]
    return sorted(courses, key=lambda course: course.dates[:1])


async def patch_dates(
    course_id: str, add: list[date], remove: list[date]
) -> RosterCourse | None:
    """Add and remove a course's dates, leaving any answers in place.

    Args:
        course_id: The course's id.
        add: Dates to put on the course.
        remove: Dates to take off it.

    Returns:
        The updated course, or None if there is no such course.
    """
    return _live(await roster_months.update_dates(_ref(course_id), add, remove))


async def set_freeze(course_id: str, freeze_at: date) -> RosterCourse | None:
    """Move the date a course freezes on.

    Args:
        course_id: The course's id.
        freeze_at: The new freeze date.

    Returns:
        The updated course, or None if there is no such course.
    """
    if await get_course(course_id) is None:
        return None
    await _ref(course_id).update({"freezeAt": freeze_at.isoformat()})
    return await get_course(course_id)


async def remove_course(course_id: str, removed_by: str) -> bool:
    """Hide a course, keeping it and every answer on its dates.

    Args:
        course_id: The course's id.
        removed_by: The admin removing it.

    Returns:
        False if there was no such course to remove.
    """
    if await get_course(course_id) is None:
        return False
    await _ref(course_id).update(
        {"removedBy": removed_by, "removedAt": datetime.now(UTC).isoformat()}
    )
    return True


async def lock_dates(dates: list[date]) -> dict[date, date]:
    """Find when each date freezes, across every month and course holding it.

    Args:
        dates: The dates to look up.

    Returns:
        The earliest freeze date for each date on a grid; others are left out.
    """
    wanted = set(dates)
    months = await roster_months.get_months(
        {roster_months.owner_month(day) for day in wanted}
    )
    locks: dict[date, date] = {}
    for grid in [*months, *await list_courses()]:
        for day in wanted.intersection(grid.dates):
            locks[day] = min(locks.get(day, grid.freeze_at), grid.freeze_at)
    return locks
