"""Roster endpoints: sign-in, the squadron list, and flying months.

A person signs in with a six-digit code emailed to them, and holds a session
token afterwards. See ``roster-api-contract.md`` for the full contract.
"""

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from google.api_core.exceptions import Conflict
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config import SCOPE_ROSTER_READ
from app.models.roster import Role, RosterMonth
from app.security import require_admin, require_scope, verify_session
from app.services import roster_auth, roster_months

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
        HTTPException: 400 for a malformed month, 404 if it does not exist.
    """
    _month_start(month)
    record = await roster_months.set_freeze(month, request.freeze_at)
    if record is None:
        raise _unknown_month()

    logger.info("Month %s freeze moved by %s", month, session["personId"])
    return MonthResponse.of(record)
