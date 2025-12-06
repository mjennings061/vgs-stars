"""MongoDB database service for notification storage and retrieval.

Provides functions for connecting to MongoDB and performing CRUD operations
on notification collections with proper indexing.
"""

import logging
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import get_settings
from app.models.notifications import Notification, NotificationBatch, NotificationStatus

logger = logging.getLogger(__name__)

# Global MongoDB client instance
_client: MongoClient | None = None


def get_client() -> MongoClient:
    """Get or create the MongoDB client singleton.

    Returns:
        MongoClient instance.
    """
    global _client
    if _client is None:
        settings = get_settings()
        logger.info("Connecting to MongoDB...")
        _client = MongoClient(settings.mongo.uri)
        logger.info("MongoDB connection established")
    return _client


def get_database() -> Database:
    """Get the STARS database.

    Returns:
        Database instance for STARS data.
    """
    settings = get_settings()
    client = get_client()
    return client[settings.mongo.db_name]


def get_collection(name: str) -> Collection:
    """Get a collection by name from the STARS database.

    Args:
        name: Collection name.

    Returns:
        Collection instance.
    """
    db = get_database()
    return db[name]


def ensure_indexes() -> None:
    """Create database indexes for performance and deduplication.

    Creates indexes on:
    - auths_notification: (authId, userId, sentAt) for deduplication
    - auth_notification_batches: (userId, sentAt) for user history
    - expiring_auths: (resourceId) for resource lookup
    """
    settings = get_settings()
    db = get_database()

    logger.info("Ensuring database indexes...")

    # Notifications collection indexes
    notifications_col = db[settings.mongo.notifications_collection]
    notifications_col.create_index(
        [("authId", ASCENDING), ("userId", ASCENDING), ("sentAt", DESCENDING)],
        name="auth_user_sent_idx",
    )

    # Notification batches collection indexes
    batches_col = db[settings.mongo.notification_batches_collection]
    batches_col.create_index(
        [("userId", ASCENDING), ("sentAt", DESCENDING)],
        name="user_sent_idx",
    )

    # Expiring auths collection indexes
    expiring_col = db[settings.mongo.expiring_auths_collection]
    expiring_col.create_index(
        [("resourceId", ASCENDING)],
        name="resource_idx",
    )

    logger.info("Database indexes created successfully")


def save_notification(notification: Notification) -> str:
    """Insert a notification document into MongoDB.

    Args:
        notification: Notification instance to save.

    Returns:
        Inserted document ID as string.
    """
    settings = get_settings()
    col = get_collection(settings.mongo.notifications_collection)

    doc = notification.model_dump(by_alias=True)
    result = col.insert_one(doc)

    logger.debug("Saved notification: %s", result.inserted_id)
    return str(result.inserted_id)


def save_notification_batch(batch: NotificationBatch) -> str:
    """Insert a notification batch document into MongoDB.

    Args:
        batch: NotificationBatch instance to save.

    Returns:
        Inserted document ID as string.
    """
    settings = get_settings()
    col = get_collection(settings.mongo.notification_batches_collection)

    doc = batch.model_dump(by_alias=True)
    result = col.insert_one(doc)

    logger.info(
        "Saved notification batch for user %s: %s", batch.user_email, result.inserted_id
    )
    return str(result.inserted_id)


def get_pending_notifications() -> list[dict[str, Any]]:
    """Query pending notifications from MongoDB.

    Returns:
        List of pending notification documents.
    """
    settings = get_settings()
    col = get_collection(settings.mongo.notifications_collection)

    notifications = list(col.find({"status": NotificationStatus.PENDING.value}))
    logger.debug("Found %d pending notifications", len(notifications))
    return notifications


def update_notification_status(
    notification_id: str,
    status: NotificationStatus,
    error: str | None = None,
) -> None:
    """Update the status of a notification.

    Args:
        notification_id: MongoDB document ID.
        status: New notification status.
        error: Optional error message if status is FAILED.
    """
    settings = get_settings()
    col = get_collection(settings.mongo.notifications_collection)

    update_doc = {"status": status.value}
    if error:
        update_doc["error"] = error

    col.update_one({"_id": ObjectId(notification_id)}, {"$set": update_doc})
    logger.debug("Updated notification %s to %s", notification_id, status.value)


def get_notifications_for_auth(auth_id: int) -> list[dict[str, Any]]:
    """Get all notifications for a specific authorisation (deduplication check).

    Args:
        auth_id: Authorisation ID.

    Returns:
        List of notification documents for this authorisation.
    """
    settings = get_settings()
    col = get_collection(settings.mongo.notifications_collection)

    notifications = list(col.find({"authId": auth_id}))
    logger.debug(
        "Found %d existing notifications for auth %d", len(notifications), auth_id
    )
    return notifications


def close_client() -> None:
    """Close the MongoDB client connection gracefully."""
    global _client
    if _client is not None:
        logger.info("Closing MongoDB connection...")
        _client.close()
        _client = None
        logger.info("MongoDB connection closed")
