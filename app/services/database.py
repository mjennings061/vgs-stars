"""MongoDB database service for notification storage and retrieval.

Provides functions for connecting to MongoDB and performing CRUD operations
on notification collections with proper indexing.
"""

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import get_settings
from app.models.notifications import Notification, NotificationBatch

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
    - users: (api_key) for fast API key lookup
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

    # Users collection indexes (hashed API key)
    users_col = db[settings.mongo.users_collection]
    users_col.create_index(
        [("api_key", ASCENDING)],
        name="api_key_unique",
        unique=True,
    )

    logger.info("Database indexes created successfully")


def save_notification_with_batch(
    batch: NotificationBatch, notifications: list[Notification]
) -> tuple[str, list[str]]:
    """Save notification batch and individual notifications atomically.

    Uses MongoDB transactions to ensure both batch and individual notifications
    are saved together, maintaining consistency for deduplication.

    Args:
        batch: NotificationBatch instance to save.
        notifications: List of individual Notification instances.

    Returns:
        Tuple of (batch_id, list of notification_ids).

    Raises:
        Exception: If transaction fails, all changes are rolled back.
    """
    settings = get_settings()
    client = get_client()

    batch_col = get_collection(settings.mongo.notification_batches_collection)
    notif_col = get_collection(settings.mongo.notifications_collection)

    with client.start_session() as session:
        with session.start_transaction():
            # Save batch
            batch_doc = batch.model_dump(by_alias=True)
            batch_result = batch_col.insert_one(batch_doc, session=session)

            # Save individual notifications
            notif_ids = []
            for notification in notifications:
                notif_doc = notification.model_dump(by_alias=True)
                notif_result = notif_col.insert_one(notif_doc, session=session)
                notif_ids.append(str(notif_result.inserted_id))

            logger.info(
                "Saved notification batch for user %s with %d individual "
                "notifications: %s",
                batch.user_email,
                len(notifications),
                batch_result.inserted_id,
            )

            return str(batch_result.inserted_id), notif_ids


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
