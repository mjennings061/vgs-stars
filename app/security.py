"""Security dependencies for the FastAPI application."""

import logging

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import API_KEY_HEADER_NAME
from app.services import api_keys

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

    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "API-Key"},
        )

    return {"source": "firestore", "name": record.get("name")}
