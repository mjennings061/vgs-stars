"""Security dependencies for the FastAPI application."""

import logging

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.config import API_KEY_HEADER_NAME
from app.services import api_keys, roster_auth

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    description="API key required to access protected endpoints",
    auto_error=False,
)


async def verify_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> dict:
    """Validate API key from header using Firestore-backed records only."""
    # Allow dynamic header name from settings if different to default
    if not api_key and request:
        api_key = request.headers.get(API_KEY_HEADER_NAME)

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "API-Key"},
        )

    # Firestore-backed keys (no static shortcuts)
    try:
        record = await api_keys.resolve_api_key(api_key)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("API key validation failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable",
            headers={"WWW-Authenticate": "API-Key"},
        ) from exc

    # A key with no scopes is refused everywhere rather than assumed harmless.
    if not record or not record.get("scopes"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "API-Key"},
        )

    return {
        "source": "firestore",
        "name": record.get("name"),
        "scopes": record["scopes"],
    }


def require_scope(scope: str):
    """Build a dependency that requires an API key carrying a scope.

    Args:
        scope: Scope the caller's key must list.

    Returns:
        A FastAPI dependency yielding the caller record.
    """

    async def dependency(caller: dict = Depends(verify_api_key)) -> dict:
        """Reject a valid key that is not allowed to do this."""
        if scope not in caller["scopes"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient scope",
            )
        return caller

    return dependency


bearer_scheme = HTTPBearer(
    description="Roster session token from sign-in",
    auto_error=False,
)


async def verify_session(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict:
    """Resolve a roster session from the Authorization header.

    Args:
        credentials: Bearer credentials parsed from the request.

    Returns:
        The session record, including person, name and role.

    Raises:
        HTTPException: 401 if the token is absent, unknown or expired.
    """
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign-in required",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not credentials:
        raise unauthorised

    try:
        session = await roster_auth.resolve_session(credentials.credentials)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Session lookup failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication unavailable",
        ) from exc

    if session is None:
        raise unauthorised

    # The route needs the plain token to delete exactly this session on logout.
    return {**session, "token": credentials.credentials}
