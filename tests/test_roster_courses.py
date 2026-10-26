"""Behavioural tests for courses, and dates that sit on a month and a course."""

import os
from datetime import date, timedelta

import pytest

from app.config import ROSTER_MONTHS_COLLECTION, STARS_ORG_UNIT_ID
from tests.conftest import HEADERS, MEMBER_ID

pytestmark = pytest.mark.skipif(
    "FIRESTORE_EMULATOR_HOST" not in os.environ,
    reason="needs the Firestore emulator: gcloud emulators firestore start",
)

# Tue 27 Jul to Thu 5 Aug 2027; Sun 1 Aug is stored under July.
SUMMER = [date(2027, 7, 27) + timedelta(days=n) for n in range(10)]
MONTH = "2027-03"
SATURDAY = "2027-03-06"
SUNDAY = "2027-03-07"


def _send(client, method: str, path: str, auth: dict, body=None):
    """Call the API as the dashboard does, with the key and a session.

    Args:
        client: The test client.
        method: HTTP method.
        path: API path.
        auth: The caller's Authorization header.
        body: JSON body, if any.

    Returns:
        The HTTP response.
    """
    return client.request(method, path, json=body, headers={**HEADERS, **auth})


def _course(client, admin_auth, dates: list[date], title="Summer camp") -> str:
    """Open a course.

    Args:
        client: The test client.
        admin_auth: An admin's Authorization header.
        dates: Every date on it.
        title: What it is called.

    Returns:
        The new course's id.
    """
    body = {"title": title, "dates": [day.isoformat() for day in dates]}
    response = _send(client, "POST", "/roster/courses", admin_auth, body)
    assert response.status_code == 200, response.text
    return response.json()["courseId"]


def _answer(client, auth, day: str, status: str, comment=None):
    """Set the member's answer for one date.

    Args:
        client: The test client.
        auth: The caller's Authorization header.
        day: The date.
        status: Y, N, TBC or C.
        comment: Optional comment or reason.

    Returns:
        The HTTP response.
    """
    path = f"/roster/people/{MEMBER_ID}/days/{day}"
    body = {"status": status, "part": None, "comment": comment}
    return _send(client, "PUT", path, auth, body)


def _grid(client, path: str) -> dict:
    """Read a grid.

    Args:
        client: The test client.
        path: The grid's API path.

    Returns:
        The grid body.
    """
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_course_across_a_month_end_reads_both_months(client, admin_auth):
    """Course-only dates save, and the course grid finds them in July and August."""
    course_id = _course(client, admin_auth, SUMMER)
    for day in ("2027-07-31", "2027-08-01", "2027-08-04"):
        assert _answer(client, admin_auth, day, "Y").status_code == 200

    grid = _grid(client, f"/roster/courses/{course_id}/grid")
    assert (grid["courseId"], grid["title"], grid["month"]) == (
        course_id,
        "Summer camp",
        None,
    )
    assert grid["freezeAt"] == "2027-07-13"
    row = next(row for row in grid["rows"] if row["personId"] == MEMBER_ID)
    assert set(row["entries"]) == {"2027-07-31", "2027-08-01", "2027-08-04"}

    [listed] = client.get("/roster/courses", headers=HEADERS).json()["courses"]
    assert listed == {
        "courseId": course_id,
        "title": "Summer camp",
        "firstDate": "2027-07-27",
        "lastDate": "2027-08-05",
    }


def test_a_date_on_no_grid_is_refused(client, member_auth):
    """Nothing to answer, so nothing is written."""
    assert _answer(client, member_auth, "2027-07-31", "Y").status_code == 400


def test_a_locked_course_locks_the_date_on_an_open_month(
    client, seed, admin_auth, member_auth
):
    """Rule 12: the month is open, the course is not, so the change waits."""
    seed.collection(ROSTER_MONTHS_COLLECTION).document(
        f"{STARS_ORG_UNIT_ID}:{MONTH}"
    ).set(
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "month": MONTH,
            "dates": [SATURDAY, SUNDAY],
            "freezeAt": (date.today() + timedelta(days=30)).isoformat(),
        }
    )
    course_id = _course(client, admin_auth, [date.fromisoformat(SATURDAY)])
    # Fixed and past, so it stays within a year of the course whenever this runs.
    path = f"/roster/courses/{course_id}/freeze"
    response = _send(client, "PATCH", path, admin_auth, {"freezeAt": "2026-10-01"})
    assert response.status_code == 200, response.text

    month = _grid(client, f"/roster/months/{MONTH}/grid")
    assert month["frozen"] is False
    assert month["frozenDates"] == [SATURDAY]

    assert _answer(client, admin_auth, SATURDAY, "Y").status_code == 200
    assert _answer(client, member_auth, SATURDAY, "N").status_code == 422
    response = _answer(client, member_auth, SATURDAY, "N", comment="Work")
    assert response.json()["applied"] is False
    assert _answer(client, member_auth, SUNDAY, "N").json() == {"applied": True}

    # Saturday is on both grids, so both show one answer and one waiting change.
    for path in (f"/roster/months/{MONTH}/grid", f"/roster/courses/{course_id}/grid"):
        rows = _grid(client, path)["rows"]
        row = next(row for row in rows if row["personId"] == MEMBER_ID)
        assert row["entries"][SATURDAY]["status"] == "Y"
        assert row["pending"][SATURDAY]["toStatus"] == "N"


def test_dates_freeze_and_remove_answer_as_the_spec_says(
    client, admin_auth, member_auth
):
    """Edit, move and remove a course; its answers outlive it (rule 10)."""
    course_id = _course(client, admin_auth, SUMMER)
    assert _answer(client, member_auth, "2027-07-31", "N").status_code == 200
    base = f"/roster/courses/{course_id}"

    body = {"remove": ["2027-08-05"]}
    response = _send(client, "PATCH", f"{base}/dates", admin_auth, body)
    assert response.status_code == 200, response.text
    assert response.json()["dates"][-1] == "2027-08-04"

    everything = {"remove": [day.isoformat() for day in SUMMER]}
    response = _send(client, "PATCH", f"{base}/dates", admin_auth, everything)
    assert response.status_code == 400

    body = {"freezeAt": "2027-07-01"}
    response = _send(client, "PATCH", f"{base}/freeze", admin_auth, body)
    assert response.json()["freezeAt"] == "2027-07-01"
    body = {"freezeAt": "2029-07-01"}
    assert _send(client, "PATCH", f"{base}/freeze", admin_auth, body).status_code == 400

    assert _send(client, "DELETE", base, admin_auth).status_code == 200
    assert client.get(base, headers=HEADERS).status_code == 404
    assert _send(client, "DELETE", base, admin_auth).status_code == 404
    assert client.get("/roster/courses", headers=HEADERS).json()["courses"] == []
    assert _answer(client, member_auth, "2027-07-31", "Y").status_code == 400

    again = _course(client, admin_auth, SUMMER)
    grid = _grid(client, f"/roster/courses/{again}/grid")
    row = next(row for row in grid["rows"] if row["personId"] == MEMBER_ID)
    assert row["entries"]["2027-07-31"]["status"] == "N"


def test_a_course_needs_dates_and_a_short_title(client, admin_auth):
    """Nothing to fly is a mistake; the title has to fit the grid's header."""
    body = {"title": "Camp", "dates": []}
    assert _send(client, "POST", "/roster/courses", admin_auth, body).status_code == 400
    body = {"title": "x" * 61, "dates": ["2027-07-27"]}
    assert _send(client, "POST", "/roster/courses", admin_auth, body).status_code == 422


def test_members_and_keyless_callers_cannot_change_courses(
    client, admin_auth, member_auth
):
    """Every course write is an admin's, and only through the dashboard."""
    course_id = _course(client, admin_auth, SUMMER)
    base = f"/roster/courses/{course_id}"
    writes = [
        ("POST", "/roster/courses", {"title": "Mine", "dates": ["2027-07-27"]}),
        ("PATCH", f"{base}/dates", {"add": ["2027-08-06"]}),
        ("PATCH", f"{base}/freeze", {"freezeAt": "2027-07-01"}),
        ("DELETE", base, None),
    ]
    for method, path, body in writes:
        assert _send(client, method, path, member_auth, body).status_code == 403
        response = client.request(method, path, json=body, headers=admin_auth)
        assert response.status_code == 401
