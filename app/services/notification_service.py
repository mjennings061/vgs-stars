"""Notification service for checking expiring authorisations and sending notifications.

This module contains the core business logic for processing expiring authorisations,
grouping them by person, and coordinating email notifications.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.config import get_settings
from app.models.notifications import (
    AuthSummary,
    NotificationBatch,
    NotificationStatus,
    NotificationType,
)
from app.models.stars import Auth, User
from app.services import database, email_service, stars_client

logger = logging.getLogger(__name__)


def group_auths_by_person(auths: list[Auth]) -> dict[str, list[Auth]]:
    """Group authorisations by the person (resource ID).

    Args:
        auths: List of Auth objects to group.

    Returns:
        Dictionary mapping resource_id to list of Auth objects.
    """
    logger.debug("Grouping %d auths by person", len(auths))

    auth_groups: dict[str, list[Auth]] = defaultdict(list)
    for auth in auths:
        auth_groups[auth.resource_id].append(auth)

    logger.debug("Grouped auths into %d person groups", len(auth_groups))
    return dict(auth_groups)


def should_send_notification(auth: Auth) -> bool:
    """Check if a notification should be sent for this authorisation.

    Performs deduplication check to avoid sending duplicate notifications.

    Args:
        auth: Authorisation to check.

    Returns:
        True if notification should be sent, False if already sent.
    """
    # Check if we've already sent a notification for this auth
    existing_notifications = database.get_notifications_for_auth(auth.id)

    # Filter for sent or pending notifications
    for notif in existing_notifications:
        if notif.get("status") in [
            NotificationStatus.SENT.value,
            NotificationStatus.PENDING.value,
        ]:
            logger.debug("Notification already exists for auth %d, skipping", auth.id)
            return False

    return True


def create_notification_batch(
    resource_id: str,
    auths: list[Auth],
    user: User,
    notification_type: NotificationType,
) -> NotificationBatch:
    """Create a notification batch for multiple authorisations.

    Args:
        resource_id: Resource ID for the person.
        auths: List of authorisations for this person.
        user: User object with email details.
        notification_type: Type of notification to send.

    Returns:
        NotificationBatch instance ready to send.
    """
    # Create auth summaries
    auth_summaries = [
        AuthSummary(
            authId=auth.id,
            mapId=auth.map_id,
            authName=auth.map_name,
            expiryDate=auth.expiry,
        )
        for auth in auths
        if auth.expiry
    ]

    # Determine subject based on notification type
    auth_count = len(auth_summaries)
    if notification_type == NotificationType.EXPIRING_SOON:
        subject = (
            f"STARS Authorisations Expiring Soon - Action Required "
            f"({auth_count} auth{'s' if auth_count != 1 else ''})"
        )
    else:
        subject = (
            f"STARS Authorisations Expired - Urgent Action Required "
            f"({auth_count} auth{'s' if auth_count != 1 else ''})"
        )

    # Get resource name from first auth
    resource_name = auths[0].resource_name if auths else resource_id

    batch = NotificationBatch(
        userId=user.id,
        userEmail=user.email,
        resourceId=resource_id,
        resourceName=resource_name,
        notificationType=notification_type,
        subject=subject,
        status=NotificationStatus.PENDING,
        auths=auth_summaries,
    )

    return batch


def check_and_notify_expiring_auths(
    unit_id: str | None = None, warning_days: int | None = None
) -> dict:
    """Check for expiring authorisations and send notifications.

    Main function that coordinates the entire notification workflow:
    1. Fetch expiring auths from STARS API
    2. Group by person
    3. Check for deduplication
    4. Send email notifications
    5. Save to database

    Args:
        unit_id: Organisation unit ID (defaults to config).
        warning_days: Days before expiry to warn (defaults to config).

    Returns:
        Dictionary with results summary including counts and errors.
    """
    settings = get_settings()

    # Use defaults from config if not provided
    if unit_id is None:
        unit_id = settings.stars.org_unit_id
    if warning_days is None:
        warning_days = settings.app.expiry_warning_days

    # Calculate expiry date threshold
    expiry_date = date.today() + timedelta(days=warning_days)

    logger.info(
        "Checking for auths expiring before %s for unit %s", expiry_date, unit_id
    )

    try:
        # Fetch expiring auths from STARS
        expiring_auths = stars_client.get_expiring_auths_by_date(unit_id, expiry_date)

        if not expiring_auths:
            logger.info("No expiring authorisations found")
            return {
                "success": True,
                "notifications_sent": 0,
                "notifications_failed": 0,
                "summary": {
                    "total_expiring_auths": 0,
                    "users_notified": 0,
                    "emails_sent": 0,
                },
                "errors": [],
            }

        # Group by person
        auths_by_person = group_auths_by_person(expiring_auths)

        notifications_sent = 0
        notifications_failed = 0
        errors = []

        # Process each person
        for resource_id, person_auths in auths_by_person.items():
            try:
                logger.info(
                    "Processing %d auths for %s", len(person_auths), resource_id
                )

                # Get user details
                user = stars_client.get_user_from_resource(resource_id)

                # Filter auths that need notification (deduplication)
                auths_to_notify = [
                    auth for auth in person_auths if should_send_notification(auth)
                ]

                if not auths_to_notify:
                    logger.info(
                        "All auths for %s already notified, skipping", user.email
                    )
                    continue

                # Determine notification type (expiring soon vs expired)
                today = date.today()
                has_expired = any(
                    auth.expiry and auth.expiry < today for auth in auths_to_notify
                )
                notification_type = (
                    NotificationType.EXPIRED
                    if has_expired
                    else NotificationType.EXPIRING_SOON
                )

                # Create notification batch
                batch = create_notification_batch(
                    resource_id, auths_to_notify, user, notification_type
                )

                # Send email
                try:
                    email_service.send_notification_email(batch)
                    batch.status = NotificationStatus.SENT
                    batch.sent_at = datetime.now()
                    notifications_sent += 1
                    logger.info("Notification sent to %s", user.email)
                except Exception as e:
                    batch.status = NotificationStatus.FAILED
                    batch.error = str(e)
                    notifications_failed += 1
                    errors.append(f"Failed to send email to {user.email}: {e}")
                    logger.error("Failed to send notification: %s", e)

                # Save batch to database
                database.save_notification_batch(batch)

            except Exception as e:
                notifications_failed += 1
                error_msg = f"Failed to process notifications for {resource_id}: {e}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)

        # Return summary
        result = {
            "success": True,
            "notifications_sent": notifications_sent,
            "notifications_failed": notifications_failed,
            "summary": {
                "total_expiring_auths": len(expiring_auths),
                "users_notified": len(auths_by_person),
                "emails_sent": notifications_sent,
            },
            "errors": errors,
        }

        logger.info(
            "Notification check complete: %d sent, %d failed",
            notifications_sent,
            notifications_failed,
        )
        return result

    except Exception as e:
        logger.error("Fatal error in notification check: %s", e, exc_info=True)
        return {
            "success": False,
            "notifications_sent": 0,
            "notifications_failed": 0,
            "summary": {
                "total_expiring_auths": 0,
                "users_notified": 0,
                "emails_sent": 0,
            },
            "errors": [str(e)],
        }


def notify_expiring_auths_for_resource(
    resource_id: str, unit_id: str | None = None, warning_days: int | None = None
) -> dict:
    """Send expiring authorisation notifications for a single resource.

    This endpoint intentionally skips deduplication and persistence; it simply
    checks the user's expiring auths and attempts to send one email.

    Args:
        resource_id: STARS resource ID for the person (e.g., "R:125129").
        unit_id: Optional organisation unit ID (defaults to config).
        warning_days: Days before expiry to warn (defaults to config).

    Returns:
        Dictionary with results summary including counts and errors.
    """
    settings = get_settings()

    if unit_id is None:
        unit_id = settings.stars.org_unit_id
    if warning_days is None:
        warning_days = settings.app.expiry_warning_days

    expiry_date = date.today() + timedelta(days=warning_days)
    target_org_unit_id = str(unit_id) if unit_id is not None else None

    logger.info(
        "Checking expiring auths for %s before %s (unit %s)",
        resource_id,
        expiry_date,
        unit_id,
    )

    try:
        # Fetch current auths for the user and filter by expiry threshold
        user_auths = stars_client.get_eng_auths_for_user(resource_id)
        expiring_auths = [
            auth
            for auth in user_auths
            if auth.expiry
            and auth.expiry <= expiry_date
            and (
                target_org_unit_id is None
                or str(auth.org_unit_id) == str(target_org_unit_id)
            )
        ]

        if not expiring_auths:
            logger.info("No expiring authorisations found for %s", resource_id)
            return {
                "success": True,
                "notifications_sent": 0,
                "notifications_failed": 0,
                "summary": {
                    "total_expiring_auths": 0,
                    "users_notified": 0,
                    "emails_sent": 0,
                },
                "errors": [],
            }

        user = stars_client.get_user_from_resource(resource_id)

        today = date.today()
        has_expired = any(
            auth.expiry and auth.expiry < today for auth in expiring_auths
        )
        notification_type = (
            NotificationType.EXPIRED if has_expired else NotificationType.EXPIRING_SOON
        )

        # Create notification batch
        batch = create_notification_batch(
            resource_id, expiring_auths, user, notification_type
        )

        notifications_sent = 0
        notifications_failed = 0
        errors: list[str] = []

        # Send email
        try:
            email_service.send_notification_email(batch)
            batch.status = NotificationStatus.SENT
            batch.sent_at = datetime.now()
            notifications_sent = 1
            logger.info("Notification sent to %s for %s", user.email, resource_id)
        except Exception as e:
            batch.status = NotificationStatus.FAILED
            batch.error = str(e)
            notifications_failed = 1
            errors.append(f"Failed to send email to {user.email}: {e}")
            logger.error("Failed to send notification for %s: %s", resource_id, e)

        # Intentionally skip database persistence for this ad-hoc endpoint

        return {
            "success": True,
            "notifications_sent": notifications_sent,
            "notifications_failed": notifications_failed,
            "summary": {
                "total_expiring_auths": len(expiring_auths),
                "users_notified": 1,
                "emails_sent": notifications_sent,
            },
            "errors": errors,
        }

    except Exception as e:
        logger.error(
            "Fatal error in single-user notification check: %s", e, exc_info=True
        )
        return {
            "success": False,
            "notifications_sent": 0,
            "notifications_failed": 0,
            "summary": {
                "total_expiring_auths": 0,
                "users_notified": 0,
                "emails_sent": 0,
            },
            "errors": [str(e)],
        }
