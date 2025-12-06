"""Health check endpoints for monitoring and readiness probes.

Provides endpoints to verify the API is running and dependencies are accessible.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.services import database, stars_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    """Basic liveness check.

    Returns:
        Dictionary with status and timestamp.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().astimezone().isoformat(),
    }


@router.get("/health/ready")
async def readiness_check() -> dict:
    """Readiness check verifying database and STARS API accessibility.

    Returns:
        Dictionary with status and individual check results.

    Raises:
        HTTPException: If any dependency check fails.
    """
    checks = {}

    # Check MongoDB connection
    try:
        db = database.get_database()
        # Simple ping to verify connection
        db.command("ping")
        checks["database"] = "ok"
    except Exception as e:
        logger.error("Database readiness check failed: %s", e)
        checks["database"] = f"error: {str(e)}"

    # Check STARS API accessibility
    try:
        # Simple auth header check (doesn't make actual API call)
        header = stars_client.auth_header()
        if header and "Authorization" in header:
            checks["stars_api"] = "ok"
        else:
            checks["stars_api"] = "error: missing auth header"
    except Exception as e:
        logger.error("STARS API readiness check failed: %s", e)
        checks["stars_api"] = f"error: {str(e)}"

    # Determine overall status
    all_ok = all(check == "ok" for check in checks.values())

    if not all_ok:
        raise HTTPException(
            status_code=503,
            detail={"status": "not ready", "checks": checks},
        )

    return {
        "status": "ready",
        "checks": checks,
    }
