"""FastAPI application for STARS authorisation expiry notifications.

Main entry point for the stateless REST API designed to scale to zero when idle.
Scheduling is handled externally via cloud scheduler, cron, or similar.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routes import auths, health
from app.security import verify_api_key
from app.services import database

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """Application lifespan context manager for startup and shutdown events.

    Handles:
    - Startup: Initialise MongoDB connection and ensure indexes
    - Shutdown: Close MongoDB connection gracefully

    Args:
        fastapi_app: FastAPI application instance.

    Yields:
        Control back to FastAPI during application lifetime.
    """
    # Startup
    logger.info("Starting STARS authorisation expiry notification service...")

    # Load app
    _ = fastapi_app

    # Load settings
    _ = get_settings()

    try:
        # Initialise database connection
        database.get_client()
        logger.info("MongoDB connection initialised")

        # Ensure database indexes exist
        database.ensure_indexes()
        logger.info("Database indexes verified")

        logger.info("Application startup complete")

    except Exception as e:
        logger.error("Failed to initialise application: %s", e, exc_info=True)
        raise

    yield

    # Shutdown
    logger.info("Shutting down application...")
    try:
        database.close_client()
        logger.info("MongoDB connection closed")
    except Exception as e:
        logger.error("Error during shutdown: %s", e, exc_info=True)

    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="STARS Authorisation Expiry Notifications",
    description=(
        "API for checking and notifying about expiring STARS engineering "
        "authorisations"
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions globally.

    Args:
        request: FastAPI request object.
        exc: Exception that was raised.

    Returns:
        JSON response with error details.
    """
    logger.error(
        "Unhandled exception for %s %s: %s",
        request.method,
        request.url,
        exc,
        exc_info=True,
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error": str(exc),
        },
    )


# Register routers
app.include_router(health.router)
app.include_router(auths.router)


# Root endpoint
@app.get("/")
async def root(_: dict = Depends(verify_api_key)):
    """Root endpoint with API information.

    Returns:
        Dictionary with API details and links.
    """
    return {
        "service": "STARS Authorisation Expiry Notifications",
        "version": "0.1.0",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "readiness": "/health/ready",
            "notify_expiry": "POST /auths/notify-auth-expiry",
            "notify_expiry_single": "POST /auths/notify-auth-expiry/user",
            "list_expiring": "GET /auths/expiring",
            "test_email": "POST /auths/test-email",
            "docs": "/docs",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
