"""Authorisation expiry notification endpoints.

Provides RPC-style endpoints for checking and notifying about expiring authorisations.
"""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models.notifications import (
    AuthSummary,
    NotificationBatch,
    NotificationStatus,
    NotificationType,
)
from app.security import verify_api_key
from app.services import email_service, notification_service, stars_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auths",
    tags=["authorisations"],
    dependencies=[Depends(verify_api_key)],
)


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


class NotifySingleAuthExpiryRequest(NotifyAuthExpiryRequest):
    """Request body for single-user notify-auth-expiry endpoint."""

    resource_id: str = Field(
        description="Resource ID of the user to notify (e.g., 'R:XXXXX')"
    )


class SendNotificationRequest(BaseModel):
    """Request body for send-notification endpoint."""

    batch_id: str = Field(description="Notification batch ID to send")


@router.post("/notify-auth-expiry", response_model=NotifyAuthExpiryResponse)
async def notify_auth_expiry(
    request: NotifyAuthExpiryRequest = NotifyAuthExpiryRequest(),
) -> NotifyAuthExpiryResponse:
    """Check for expiring authorisations and send email notifications (RPC pattern).

    This endpoint orchestrates the entire notification workflow:
    1. Fetches expiring auths from STARS API
    2. Groups them by person
    3. Creates notification batch records
    4. Queues Cloud Tasks to send emails

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


@router.post("/send_notification")
async def send_notification(request: SendNotificationRequest) -> dict:
    """Send a queued notification batch by ID.

    Args:
        request: Payload containing the batch ID.

    Returns:
        Result payload describing the send outcome.
    """
    logger.info("Received send-notification request for batch %s", request.batch_id)

    try:
        # This endpoint is called by Cloud Tasks using the API key header.
        result = notification_service.send_notification_batch(request.batch_id)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to send notification batch: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send notification batch: {str(e)}",
        ) from e


@router.post("/notify-auth-expiry/user", response_model=NotifyAuthExpiryResponse)
async def notify_auth_expiry_for_user(
    request: NotifySingleAuthExpiryRequest,
) -> NotifyAuthExpiryResponse:
    """Send expiry notifications for a single user (RPC pattern).

    This endpoint skips deduplication and does not persist notification batches.

    Args:
        request: Resource ID plus optional parameters (unit_id, warning_days).

    Returns:
        Detailed results including counts and any errors.

    Raises:
        HTTPException: If the operation fails completely.
    """
    logger.info(
        (
            "Received single-user notify-auth-expiry request: "
            "resource_id=%s, unit_id=%s, warning_days=%s"
        ),
        request.resource_id,
        request.unit_id,
        request.warning_days,
    )

    try:
        result = notification_service.notify_expiring_auths_for_resource(
            resource_id=request.resource_id,
            unit_id=request.unit_id,
            warning_days=request.warning_days,
        )

        return NotifyAuthExpiryResponse(**result)

    except Exception as e:
        logger.error(
            "Fatal error in single-user notify-auth-expiry: %s", e, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to process authorisation expiry notification for",
                f"{request.resource_id}: {str(e)}",
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
