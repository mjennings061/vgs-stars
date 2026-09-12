"""Roster sign-in endpoints.

A person signs in with a six-digit code emailed to them, and holds a session
token afterwards. See ``roster-api-contract.md`` for the full contract.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import SCOPE_ROSTER_READ
from app.models.roster import Role
from app.security import require_scope, verify_session
from app.services import roster_auth

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
