"""Firestore database service for notification storage and retrieval.

Provides functions for connecting to Firestore and performing CRUD operations
on notification collections.
"""

import logging
from datetime import datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud.firestore_v1.async_client import AsyncClient

from app.config import get_settings
from app.models.notifications import (
    Notification,
    NotificationBatch,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger(__name__)

# Global Firestore async client instance
_client: AsyncClient | None = None


def get_client() -> AsyncClient:
    """Get or create the Firestore async client singleton.

    Returns:
        AsyncClient instance.
    """
    global _client
    if _client is None:
        logger.info("Connecting to Firestore...")
        _client = AsyncClient()
        logger.info("Firestore connection established")
    return _client


def get_collection(name: str) -> firestore.AsyncCollectionReference:
    """Get a collection by name from Firestore.

    Args:
        name: Collection name.

    Returns:
        AsyncCollectionReference instance.
    """
    client = get_client()
    return client.collection(name)


async def save_notification_with_batch(
    batch: NotificationBatch, notifications: list[Notification]
) -> tuple[str, list[str]]:
    """Save notification batch and individual notifications atomically.

    Uses Firestore transactions to ensure both batch and individual notifications
    are saved together, maintaining consistency for deduplication.

    Args:
        batch: NotificationBatch instance to save.
        notifications: List of individual Notification instances.

    Returns:
        Tuple of (batch_id, list of notification_ids).
    """
    settings = get_settings()
    client = get_client()

    batch_col = get_collection(settings.database.notification_batches_collection)
    notif_col = get_collection(settings.database.notifications_collection)

    @firestore.async_transactional
    async def save_in_transaction(transaction):
        """Execute the save operation within a transaction."""
        # Save batch
        batch_doc = batch.model_dump(by_alias=True, mode="json")
        batch_ref = batch_col.document()
        transaction.set(batch_ref, batch_doc)

        # Save individual notifications
        notif_ids = []
        for notification in notifications:
            notif_doc = notification.model_dump(by_alias=True, mode="json")
            notif_ref = notif_col.document()
            transaction.set(notif_ref, notif_doc)
            notif_ids.append(notif_ref.id)

        return batch_ref.id, notif_ids

    transaction = client.transaction()
    batch_id, notif_ids = await save_in_transaction(transaction)

    logger.info(
        "Saved notification batch for user %s with %d individual notifications: %s",
        batch.user_email,
        len(notifications),
        batch_id,
    )

    return batch_id, notif_ids


async def save_notification_batch(batch: NotificationBatch) -> str:
    """Save a notification batch without individual notifications.

    Args:
        batch: NotificationBatch instance to save.

    Returns:
        Inserted batch ID as a string.
    """
    settings = get_settings()
    batch_col = get_collection(settings.database.notification_batches_collection)
    batch_doc = batch.model_dump(by_alias=True, mode="json")
    doc_ref = batch_col.document()
    await doc_ref.set(batch_doc)
    logger.info(
        "Saved notification batch for user %s with id %s",
        batch.user_email,
        doc_ref.id,
    )
    return doc_ref.id


async def get_notification_batch(batch_id: str) -> dict[str, Any] | None:
    """Fetch a notification batch by ID.

    Args:
        batch_id: Notification batch ID as a string.

    Returns:
        Batch document if found, otherwise None.
    """
    settings = get_settings()
    batch_col = get_collection(settings.database.notification_batches_collection)
    doc_ref = batch_col.document(batch_id)
    doc = await doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        if data is not None:
            data["_id"] = doc.id
            return data
    return None


async def get_pending_batch_for_user(
    user_id: str, notification_type: NotificationType | None = None
) -> dict[str, Any] | None:
    """Return the most recent pending batch for a user if one exists.

    Args:
        user_id: User ID to look up.
        notification_type: Optional notification type filter.

    Returns:
        Batch document if found, otherwise None.
    """
    settings = get_settings()
    batch_col = get_collection(settings.database.notification_batches_collection)

    query = batch_col.where(filter=FieldFilter("userId", "==", user_id)).where(
        filter=FieldFilter("status", "==", NotificationStatus.PENDING.value)
    )

    if notification_type is not None:
        query = query.where(
            filter=FieldFilter("notificationType", "==", notification_type.value)
        )

    query = query.order_by("sentAt", direction=firestore.Query.DESCENDING).limit(1)

    async for doc in query.stream():
        data = doc.to_dict()
        if data is not None:
            data["_id"] = doc.id
            return data

    return None


async def finalise_notification_batch(
    batch_id: str,
    status: NotificationStatus,
    sent_at: datetime | None,
    error: str | None,
    notifications: list[Notification],
) -> None:
    """Update a batch status and insert notifications atomically.

    Args:
        batch_id: Notification batch ID as a string.
        status: Final status to set on the batch.
        sent_at: Timestamp to record on the batch.
        error: Error message to store on the batch, if any.
        notifications: Notification records to insert.
    """
    settings = get_settings()
    client = get_client()
    batch_col = get_collection(settings.database.notification_batches_collection)
    notif_col = get_collection(settings.database.notifications_collection)

    @firestore.async_transactional
    async def update_in_transaction(transaction):
        """Execute the update operation within a transaction."""
        batch_ref = batch_col.document(batch_id)
        update_fields: dict[str, Any] = {
            "status": status.value,
            "sentAt": sent_at,
            "error": error,
        }
        transaction.update(batch_ref, update_fields)

        if notifications:
            for notification in notifications:
                notif_doc = notification.model_dump(by_alias=True, mode="json")
                notif_ref = notif_col.document()
                transaction.set(notif_ref, notif_doc)

    transaction = client.transaction()
    await update_in_transaction(transaction)


async def update_notification_batch(
    batch_id: str,
    status: NotificationStatus,
    sent_at: datetime | None = None,
    error: str | None = None,
) -> bool:
    """Update batch status and metadata.

    Args:
        batch_id: Notification batch ID as a string.
        status: Status to set.
        sent_at: Optional sent timestamp.
        error: Optional error message.

    Returns:
        True if the batch was matched and updated, otherwise False.
    """
    settings = get_settings()
    batch_col = get_collection(settings.database.notification_batches_collection)
    doc_ref = batch_col.document(batch_id)

    doc = await doc_ref.get()
    if not doc.exists:
        return False

    update_fields: dict[str, Any] = {
        "status": status.value,
        "sentAt": sent_at,
        "error": error,
    }
    await doc_ref.update(update_fields)
    return True


async def get_notifications_for_auth(auth_id: int) -> list[dict[str, Any]]:
    """Get all notifications for a specific authorisation (deduplication check).

    Args:
        auth_id: Authorisation ID.

    Returns:
        List of notification documents for this authorisation.
    """
    settings = get_settings()
    col = get_collection(settings.database.notifications_collection)

    query = col.where(filter=FieldFilter("authId", "==", auth_id))

    notifications = []
    async for doc in query.stream():
        data = doc.to_dict()
        if data is not None:
            data["_id"] = doc.id
            notifications.append(data)

    logger.debug(
        "Found %d existing notifications for auth %d", len(notifications), auth_id
    )
    return notifications


async def close_client() -> None:
    """Close the Firestore async client connection gracefully."""
    global _client
    if _client is not None:
        logger.info("Closing Firestore connection...")
        _client.close()
        _client = None
        logger.info("Firestore connection closed")
