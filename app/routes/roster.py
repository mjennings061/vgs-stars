"""Roster endpoints: sign-in, the squadron, flying months and change requests.

A person signs in with a six-digit code emailed to them, and holds a session
token afterwards. See ``docs/roster-api-contract.md`` for the contract.
"""

import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from google.api_core.exceptions import Conflict
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config import ROSTER_COMMENT_MAX_LENGTH, SCOPE_ROSTER_READ
from app.models.roster import ChangeState, Role, RosterEntry, RosterMonth, Status
from app.security import require_admin, require_scope, verify_session
from app.services import roster_auth, roster_availability, roster_months

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roster", tags=["roster"])


def _invalid_code() -> HTTPException:
    """Build the one rejection every failed verification shares.

    Returns:
        The 401 to raise.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired code",
    )


class RequestCodeRequest(BaseModel):
    """Body of a sign-in code request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    person_id: str = Field(..., alias="personId")


class RequestCodeResponse(BaseModel):
    """Nonce tying an emailed code to the browser that asked for it."""

    nonce: str


class VerifyCodeRequest(BaseModel):
    """Body of a code verification request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    nonce: str
    code: str
    remember_device: bool = Field(default=False, alias="rememberDevice")


class SessionResponse(BaseModel):
    """Who is signed in and for how long."""

    model_config = ConfigDict(populate_by_name=True)

    person_id: str = Field(..., alias="personId")
    name: str
    role: Role
    expires_at: datetime = Field(..., alias="expiresAt")


class VerifyCodeResponse(SessionResponse):
    """A new session, including the token that is only ever returned once."""

    token: str


@router.post(
    "/auth/request-code",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RequestCodeResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def request_code(request: RequestCodeRequest) -> RequestCodeResponse:
    """Email a sign-in code to a person.

    Args:
        request: Payload naming the person signing in.

    Returns:
        The nonce to send back with the code.

    Raises:
        HTTPException: 429 if the person has asked three times this hour.
    """
    try:
        nonce = await roster_auth.request_code(request.person_id)
    except roster_auth.RateLimited as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many code requests, try again later",
        ) from e

    return RequestCodeResponse(nonce=nonce)


@router.post("/auth/verify-code", response_model=VerifyCodeResponse)
async def verify_code(request: VerifyCodeRequest) -> VerifyCodeResponse:
    """Exchange a code for a session token.

    Args:
        request: Payload with the nonce, the code and the remember flag.

    Returns:
        The new session and its token.

    Raises:
        HTTPException: 401 if the nonce or the code was not acceptable.
    """
    session = await roster_auth.verify_code(
        request.nonce, request.code, request.remember_device
    )
    if session is None:
        raise _invalid_code()

    return VerifyCodeResponse.model_validate(session)


@router.get("/auth/me", response_model=SessionResponse)
async def read_me(session: dict = Depends(verify_session)) -> SessionResponse:
    """Report who is signed in, for the dashboard to decide what to show.

    Args:
        session: The caller's resolved session.

    Returns:
        The same body as verification, without the token.
    """
    return SessionResponse.model_validate(session)


@router.post("/auth/logout", status_code=status.HTTP_202_ACCEPTED)
async def logout(session: dict = Depends(verify_session)) -> dict:
    """Delete this session, leaving the person's other sessions alone.

    Args:
        session: The caller's resolved session.

    Returns:
        An empty acknowledgement.
    """
    await roster_auth.delete_session(session["token"])
    logger.info("Session closed for person %s", session.get("personId"))
    return {}


def _unknown_month() -> HTTPException:
    """Build the 404 every month lookup shares.

    Returns:
        The 404 to raise.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown month")


def _month_start(month: str) -> date:
    """Parse a month out of the path, or reject the request.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The first of that month.

    Raises:
        HTTPException: 400 if the month is not exactly YYYY-MM.
    """
    try:
        return roster_months.parse_month(month)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Month must be YYYY-MM",
        ) from e


class PersonResponse(BaseModel):
    """One squadron member, listing its fields so no email can ride along."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    person_id: str = Field(..., alias="personId")
    name: str
    initials: str | None = None
    rank: str | None = None
    instruct_cat: str | None = Field(default=None, alias="instructCat")
    role: Role


class PeopleResponse(BaseModel):
    """The squadron, in name order."""

    people: list[PersonResponse]


class MonthsResponse(BaseModel):
    """The months that exist, newest first."""

    months: list[str]


class MonthResponse(BaseModel):
    """A month's dates, its freeze date, and whether it has frozen."""

    # Generated aliases keep __init__ on the field names, which per-field alias= breaks.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    month: str
    dates: list[date]
    freeze_at: date
    frozen: bool

    @classmethod
    def of(cls, record: RosterMonth) -> "MonthResponse":
        """Answer with a stored month, working out whether it has frozen.

        Args:
            record: The month as Firestore holds it.

        Returns:
            The response body.
        """
        return cls(
            month=record.month,
            dates=record.dates,
            freeze_at=record.freeze_at,
            frozen=roster_months.is_frozen(record.freeze_at),
        )


class CreateMonthRequest(BaseModel):
    """Body naming the month to open."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    month: str


class PatchDatesRequest(BaseModel):
    """Body adding and removing flying dates."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    add: list[date] = Field(default_factory=list)
    remove: list[date] = Field(default_factory=list)


class PatchFreezeRequest(BaseModel):
    """Body moving a month's freeze date."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    freeze_at: date = Field(..., alias="freezeAt")


@router.get(
    "/people",
    response_model=PeopleResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_people() -> PeopleResponse:
    """List the squadron for the grid's rows.

    Returns:
        Every cached person, without their email address.
    """
    people = await roster_auth.list_people()
    return PeopleResponse(
        people=[PersonResponse.model_validate(person) for person in people]
    )


@router.get(
    "/months",
    response_model=MonthsResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_months() -> MonthsResponse:
    """List the months the arrows can move between.

    Returns:
        Months as 2026-11, newest first.
    """
    return MonthsResponse(months=await roster_months.list_months())


@router.post("/months", response_model=MonthResponse)
async def create_month(
    request: CreateMonthRequest, session: dict = Depends(require_admin)
) -> MonthResponse:
    """Open a month, filled in with every Saturday and Sunday.

    Args:
        request: Payload naming the month.
        session: The admin doing it.

    Returns:
        The new month.

    Raises:
        HTTPException: 400 for a malformed month, 409 if it already exists.
    """
    _month_start(request.month)
    try:
        record = await roster_months.create_month(request.month)
    except Conflict as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Month already exists",
        ) from e

    logger.info("Month %s created by %s", request.month, session["personId"])
    return MonthResponse.of(record)


@router.get(
    "/months/{month}",
    response_model=MonthResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_month(month: str) -> MonthResponse:
    """Read one month's dates and freeze state.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        The month, with ``frozen`` worked out from today.

    Raises:
        HTTPException: 400 for a malformed month, 404 if it does not exist.
    """
    _month_start(month)
    record = await roster_months.get_month(month)
    if record is None:
        raise _unknown_month()
    return MonthResponse.of(record)


@router.patch("/months/{month}/dates", response_model=MonthResponse)
async def patch_month_dates(
    month: str, request: PatchDatesRequest, session: dict = Depends(require_admin)
) -> MonthResponse:
    """Add or remove flying dates, frozen month or not.

    Args:
        month: Year and month, as 2026-11.
        request: The dates to add and remove.
        session: The admin doing it.

    Returns:
        The updated month.

    Raises:
        HTTPException: 400 for a date in another month, 404 if no such month.
    """
    start = _month_start(month)
    stray = roster_months.outside_month(start, request.add + request.remove)
    if stray:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dates outside {month}: "
            + ", ".join(day.isoformat() for day in stray),
        )

    record = await roster_months.patch_dates(month, request.add, request.remove)
    if record is None:
        raise _unknown_month()

    logger.info("Month %s dates changed by %s", month, session["personId"])
    return MonthResponse.of(record)


@router.patch("/months/{month}/freeze", response_model=MonthResponse)
async def patch_month_freeze(
    month: str, request: PatchFreezeRequest, session: dict = Depends(require_admin)
) -> MonthResponse:
    """Move the date a month freezes on.

    Args:
        month: Year and month, as 2026-11.
        request: The new freeze date.
        session: The admin doing it.

    Returns:
        The updated month.

    Raises:
        HTTPException: 400 for a malformed month or a wild freeze date, 404 if
            it does not exist.
    """
    start = _month_start(month)
    # Catches a mistyped year, which would otherwise leave the month unfrozen.
    if abs((request.freeze_at - start).days) > 365:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Freeze date must be within a year of {month}",
        )

    record = await roster_months.set_freeze(month, request.freeze_at)
    if record is None:
        raise _unknown_month()

    logger.info("Month %s freeze moved by %s", month, session["personId"])
    return MonthResponse.of(record)


class EntryResponse(BaseModel):
    """One answer on the grid, listing its fields rather than echoing storage."""

    # Generated aliases keep __init__ on the field names, which per-field alias= breaks.
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    status: Status
    comment: str | None = None
    updated_by: str
    updated_by_name: str
    updated_at: datetime


class PendingResponse(BaseModel):
    """A change waiting for an admin, as the grid shows it."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    change_id: str
    to_status: Status
    reason: str | None = None


class GridRow(BaseModel):
    """One person's line across the month."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    person_id: str
    name: str
    instruct_cat: str | None = None
    entries: dict[str, EntryResponse] = Field(default_factory=dict)
    pending: dict[str, PendingResponse] = Field(default_factory=dict)


class GridResponse(BaseModel):
    """Everything the grid needs, in one call."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    month: str
    dates: list[date]
    freeze_at: date
    frozen: bool
    rows: list[GridRow]


class SetEntryRequest(BaseModel):
    """Body setting one person's answer for one date."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: Status
    comment: str | None = Field(default=None, max_length=ROSTER_COMMENT_MAX_LENGTH)


class SetEntryResponse(BaseModel):
    """Whether the answer landed, or became a request for an admin."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    applied: bool
    change_id: str | None = None


class ChangeResponse(BaseModel):
    """One logged change, listing its fields rather than echoing storage."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    change_id: str
    person_id: str
    name: str
    date: date
    from_status: Status | None = None
    to_status: Status
    reason: str | None = None
    state: ChangeState
    updated_by: str
    updated_by_name: str
    updated_at: datetime
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None


class ChangesResponse(BaseModel):
    """Changes across every grid, newest first."""

    changes: list[ChangeResponse]


class RejectRequest(BaseModel):
    """Body giving the admin's reason for turning a change down."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    comment: str = Field(..., min_length=1, max_length=ROSTER_COMMENT_MAX_LENGTH)


@router.get(
    "/months/{month}/grid",
    response_model=GridResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_grid(month: str) -> GridResponse:
    """Draw the whole month for the whole squadron.

    Args:
        month: Year and month, as 2026-11.

    Returns:
        Every person as a row, with only the dates they have answered.

    Raises:
        HTTPException: 400 for a malformed month, 404 if it does not exist.
    """
    _month_start(month)
    record = await roster_months.get_month(month)
    if record is None:
        raise _unknown_month()

    people = await roster_auth.list_people()
    answers = await roster_availability.read_month(
        month, [person.person_id for person in people]
    )

    rows = []
    for person in people:
        row = GridRow(
            person_id=person.person_id,
            name=person.name,
            instruct_cat=person.instruct_cat,
        )
        answer = answers.get(person.person_id)
        if answer:
            row.entries = {
                day: EntryResponse.model_validate(entry)
                for day, entry in answer.entries.items()
            }
            row.pending = {
                day: PendingResponse.model_validate(pending)
                for day, pending in answer.pending.items()
            }
        rows.append(row)

    return GridResponse(
        month=record.month,
        dates=record.dates,
        freeze_at=record.freeze_at,
        frozen=roster_months.is_frozen(record.freeze_at),
        rows=rows,
    )


@router.put(
    "/months/{month}/people/{person_id}/{day}",
    response_model=SetEntryResponse,
    response_model_exclude_none=True,
)
async def set_availability(
    month: str,
    person_id: str,
    day: date,
    request: SetEntryRequest,
    session: dict = Depends(verify_session),
) -> SetEntryResponse:
    """Set one person's answer for one date.

    Args:
        month: Year and month, as 2026-11.
        person_id: Whose row is being written.
        day: The flying date.
        request: The answer and its optional comment.
        session: The caller's resolved session.

    Returns:
        Whether the answer was written, or the id of the change it became.

    Raises:
        HTTPException: 400 for a malformed month or a date off the grid, 404 if
            the month or the person is unknown, 403 if the caller may not do
            this, 409 if the date already has a pending change, 422 if a
            change after the freeze gives no reason.
    """
    _month_start(month)
    record = await roster_months.get_month(month)
    if record is None:
        raise _unknown_month()

    if day not in record.dates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{day.isoformat()} is not a flying date in {month}",
        )

    if await roster_auth.get_person(person_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown person"
        )

    is_admin = session["role"] == Role.ADMIN
    if not is_admin and person_id != session["personId"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin can set someone else's availability",
        )

    try:
        change_id = await roster_availability.save(
            month,
            person_id,
            day.isoformat(),
            RosterEntry(
                status=request.status,
                comment=request.comment,
                updated_by=session["personId"],
                updated_by_name=session["name"],
                updated_at=datetime.now(UTC),
            ),
            needs_approval=not is_admin and roster_months.is_frozen(record.freeze_at),
        )
    except roster_availability.PendingExists as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{day.isoformat()} already has a change waiting for an admin",
        ) from e
    except roster_availability.ReasonRequired as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{month} is frozen, so changing an answer needs a reason",
        ) from e

    logger.info(
        "Availability for person %s on %s %s by %s",
        person_id,
        day.isoformat(),
        "requested" if change_id else "set",
        session["personId"],
    )
    return SetEntryResponse(applied=change_id is None, change_id=change_id)


@router.get(
    "/changes",
    response_model=ChangesResponse,
    dependencies=[Depends(require_scope(SCOPE_ROSTER_READ))],
)
async def read_changes(state: ChangeState | None = None) -> ChangesResponse:
    """List changes across every grid, for the changes panel.

    Args:
        state: Only changes in this state, as ``?state=pending``.

    Returns:
        The changes, newest first.
    """
    names = {
        person.person_id: person.name for person in await roster_auth.list_people()
    }
    return ChangesResponse(
        changes=[
            ChangeResponse(
                **change.model_dump(),
                change_id=change_id,
                name=names.get(change.person_id, change.person_id),
            )
            for change_id, change in await roster_availability.list_changes(state)
        ]
    )


async def _decide(
    change_id: str, session: dict, *, approve: bool, comment: str | None = None
) -> dict:
    """Approve or reject a change, turning service errors into HTTP ones.

    Args:
        change_id: The change to decide.
        session: The admin deciding.
        approve: True to write the new answer.
        comment: The admin's reason for a rejection.

    Returns:
        An empty acknowledgement.

    Raises:
        HTTPException: 404 for an unknown change, 409 if it is not pending.
    """
    try:
        await roster_availability.decide(
            change_id,
            approve=approve,
            decided_by=session["personId"],
            decided_by_name=session["name"],
            comment=comment,
        )
    except roster_availability.UnknownChange as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown change"
        ) from e
    except roster_availability.NotPending as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This change has already been decided",
        ) from e

    logger.info(
        "Change %s %s by %s",
        change_id,
        "approved" if approve else "rejected",
        session["personId"],
    )
    return {}


@router.post("/changes/{change_id}/approve")
async def approve_change(
    change_id: str, session: dict = Depends(require_admin)
) -> dict:
    """Write the requested answer and close the change.

    Args:
        change_id: The change to approve.
        session: The admin doing it.

    Returns:
        An empty acknowledgement.
    """
    return await _decide(change_id, session, approve=True)


@router.post("/changes/{change_id}/reject")
async def reject_change(
    change_id: str, request: RejectRequest, session: dict = Depends(require_admin)
) -> dict:
    """Leave the answer alone and close the change with the admin's reason.

    Args:
        change_id: The change to reject.
        request: The admin's reason.
        session: The admin doing it.

    Returns:
        An empty acknowledgement.
    """
    return await _decide(change_id, session, approve=False, comment=request.comment)
