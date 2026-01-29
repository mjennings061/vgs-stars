"""Pydantic models for notification data and Firestore documents.

These models represent notification records stored in Firestore,
including individual notifications and batched notifications for email sending.
"""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class NotificationType(str, Enum):
    """Types of notifications that can be sent."""

    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"


class NotificationStatus(str, Enum):
    """Status of a notification."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(BaseModel):
    """Individual notification document for Firestore.

    Represents a single notification for one authorisation expiring for one user.
    Used for deduplication and tracking.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    user_id: str = Field(alias="userId", description="User ID")
    user_email: str = Field(alias="userEmail", description="User email address")
    resource_id: str = Field(alias="resourceId", description="Resource ID")
    resource_name: str = Field(alias="resourceName", description="Resource name")

    # Auth details
    auth_id: int = Field(alias="authId", description="Authorisation ID")
    map_id: int = Field(alias="mapId", description="Authorisation map ID")
    auth_name: str = Field(alias="authName", description="Authorisation name/title")
    expiry_date: date = Field(alias="expiryDate", description="Expiry date")

    # Notification metadata
    notification_type: NotificationType = Field(
        alias="notificationType", description="Type of notification"
    )
    sent_at: datetime | None = Field(
        default=None, alias="sentAt", description="When notification was sent"
    )
    status: NotificationStatus = Field(
        default=NotificationStatus.PENDING, description="Notification status"
    )
    error: str | None = Field(default=None, description="Error message if failed")

    @field_serializer("expiry_date")
    def serialise_date_to_datetime(self, value: date) -> datetime:
        """Convert date to datetime for Firestore compatibility."""
        return datetime.combine(value, datetime.min.time())


class AuthSummary(BaseModel):
    """Summary of an authorisation for batched notifications."""

    auth_id: int = Field(alias="authId")
    map_id: int = Field(alias="mapId")
    auth_name: str = Field(alias="authName")
    expiry_date: date = Field(alias="expiryDate")

    model_config = ConfigDict(populate_by_name=True)

    @field_serializer("expiry_date")
    def serialise_date_to_datetime(self, value: date) -> datetime:
        """Convert date to datetime for Firestore compatibility."""
        return datetime.combine(value, datetime.min.time())


class NotificationBatch(BaseModel):
    """Batched notification document for Firestore.

    Represents multiple authorisations expiring for one user,
    batched into a single email notification.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # User details
    user_id: str = Field(alias="userId", description="User ID")
    user_email: str = Field(alias="userEmail", description="User email address")
    resource_id: str = Field(alias="resourceId", description="Resource ID")
    resource_name: str = Field(alias="resourceName", description="Resource name")

    # Notification metadata
    notification_type: NotificationType = Field(
        alias="notificationType", description="Type of notification"
    )
    sent_at: datetime | None = Field(
        default=None, alias="sentAt", description="When email was sent"
    )
    subject: str = Field(description="Email subject line")
    status: NotificationStatus = Field(
        default=NotificationStatus.PENDING, description="Overall email status"
    )
    error: str | None = Field(default=None, description="Error message if failed")

    # Authorisations in this batch
    auths: list[AuthSummary] = Field(
        description="List of authorisations in this notification"
    )
