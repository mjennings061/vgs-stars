"""Pydantic model for API users stored in Firestore."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiUser(BaseModel):
    """API user record with hashed API key."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(..., description="User name/label for API access")
    api_key: str = Field(..., description="SHA-256 hash of the API key")
    created_at: datetime = Field(
        default_factory=datetime.now,
        alias="createdAt",
        description="When the key was issued",
    )
