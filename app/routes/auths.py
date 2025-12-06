"""Authorisation expiry notification endpoints.

Provides RPC-style endpoints for checking and notifying about expiring authorisations.
"""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models.notifications import (
    AuthSummary,
    NotificationBatch,
    NotificationStatus,
    NotificationType,
)
from app.services import email_service, notification_service, stars_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auths", tags=["authorisations"])


class NotifyAuthExpiryRequest(BaseModel):
    """Request body for notify-auth-expiry endpoint."""

    unit_id: str | None = Field(
        default=None,
        description="Organisation unit ID (defaults to config)",
    )
    warning_days: int | None = Field(
        default=None,
        description="Days before expiry to warn (defaults to config)",
    )


class NotifyAuthExpiryResponse(BaseModel):
    """Response from notify-auth-expiry endpoint."""

    success: bool
    notifications_sent: int
    notifications_failed: int
    summary: dict
    errors: list[str]


@router.post("/notify-auth-expiry", response_model=NotifyAuthExpiryResponse)
async def notify_auth_expiry(
    request: NotifyAuthExpiryRequest = NotifyAuthExpiryRequest(),
) -> NotifyAuthExpiryResponse:
    """Check for expiring authorisations and send email notifications (RPC pattern).

    This endpoint orchestrates the entire notification workflow:
    1. Fetches expiring auths from STARS API
    2. Groups them by person
    3. Sends email notifications
    4. Saves notification history to MongoDB

    Args:
        request: Optional parameters (unit_id, warning_days).

    Returns:
        Detailed results including counts and any errors.

    Raises:
        HTTPException: If the operation fails completely.
    """
    logger.info(
        "Received notify-auth-expiry request: unit_id=%s, warning_days=%s",
        request.unit_id,
        request.warning_days,
    )

    try:
        result = notification_service.check_and_notify_expiring_auths(
            unit_id=request.unit_id,
            warning_days=request.warning_days,
        )

        return NotifyAuthExpiryResponse(**result)

    except Exception as e:
        logger.error("Fatal error in notify-auth-expiry: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to process authorisation expiry " f"notifications: {str(e)}"
            ),
        ) from e


@router.get("/expiring")
async def list_expiring_auths(
    unit_id: str | None = None,
    warning_days: int | None = None,
) -> dict:
    """List expiring authorisations without sending notifications (for debugging).

    Args:
        unit_id: Organisation unit ID (defaults to config).
        warning_days: Days before expiry to check (defaults to config).

    Returns:
        Dictionary with expiring authorisations list.

    Raises:
        HTTPException: If the query fails.
    """
    settings = get_settings()

    # Use defaults from config if not provided
    if unit_id is None:
        unit_id = settings.stars.org_unit_id
    if warning_days is None:
        warning_days = settings.app.expiry_warning_days

    expiry_date = date.today() + timedelta(days=warning_days)

    logger.info("Listing expiring auths for unit %s before %s", unit_id, expiry_date)

    try:
        auths = stars_client.get_expiring_auths_by_date(unit_id, expiry_date)

        # Convert to dict for JSON response
        auths_data = [auth.model_dump() for auth in auths]

        return {
            "unit_id": unit_id,
            "expiry_date": expiry_date.isoformat(),
            "warning_days": warning_days,
            "count": len(auths_data),
            "auths": auths_data,
        }

    except Exception as e:
        logger.error("Failed to list expiring auths: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve expiring authorisations: {str(e)}",
        ) from e


class TestEmailRequest(BaseModel):
    """Request body for test-email endpoint."""

    email: str = Field(description="Email address to send test to")
    resource_id: str = Field(description="Resource ID to fetch test auths for")


@router.post("/test-email")
async def send_test_email(request: TestEmailRequest) -> dict:
    """Send a test notification email (for debugging).

    Args:
        request: Email address and resource ID.

    Returns:
        Dictionary with success message.

    Raises:
        HTTPException: If sending fails.
    """
    logger.info(
        "Sending test email to %s for resource %s", request.email, request.resource_id
    )

    try:
        # Create a test batch with sample data
        batch = NotificationBatch(
            userId="test-user-id",
            userEmail=request.email,
            resourceId=request.resource_id,
            resourceName="Test User",
            notificationType=NotificationType.EXPIRING_SOON,
            subject="STARS Authorisations Expiring Soon - Test Email",
            status=NotificationStatus.PENDING,
            auths=[
                AuthSummary(
                    authId=999999,
                    mapId=3209,
                    authName="TEST01 Test Authorisation",
                    expiryDate=date.today() + timedelta(days=15),
                )
            ],
        )

        # Send the email
        email_service.send_notification_email(batch)

        return {
            "message": "Test email sent successfully",
            "email": request.email,
            "resource_id": request.resource_id,
        }

    except Exception as e:
        logger.error("Failed to send test email: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send test email: {str(e)}",
        ) from e
