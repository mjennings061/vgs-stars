"""Security dependencies for the FastAPI application."""

import logging

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.services import api_keys

logger = logging.getLogger(__name__)

# Default header name used for OpenAPI docs; runtime config may override.
DEFAULT_API_KEY_HEADER = "X-API-Key"
api_key_header = APIKeyHeader(
    name=DEFAULT_API_KEY_HEADER,
    description="API key required to access protected endpoints",
    auto_error=False,
)


async def verify_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> dict:
    """Validate API key from header using Firestore-backed records only."""
    settings = get_settings()
    header_name = settings.app.api_key_header_name or DEFAULT_API_KEY_HEADER

    # Allow dynamic header name from settings if different to default
    if not api_key and request:
        api_key = request.headers.get(header_name)

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
