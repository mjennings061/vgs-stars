"""Pydantic models for roster documents stored in Firestore."""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# Generated aliases keep __init__ on the field names, which per-field alias= breaks.
DOCUMENT = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")


class Role(str, Enum):
    """What a signed-in person is allowed to do."""

    MEMBER = "member"
    ADMIN = "admin"


class RosterPerson(BaseModel):
    """A squadron member cached from STARS, plus their roster role."""

    model_config = DOCUMENT

    squadron_id: str
    person_id: str
    name: str
    email: str | None = None
    # Field names match stars.Person, so the sync job is a straight copy.
    initials: str | None = None
    rank: str | None = None
    instruct_cat: str | None = None
    role: Role = Role.MEMBER


class RosterCode(BaseModel):
    """A pending sign-in code, keyed in Firestore by the hash of its nonce."""

    model_config = DOCUMENT

    squadron_id: str
    person_id: str
    code_hash: str = Field(..., description="SHA-256 of the code")
    attempts: int = 0
    used: bool = False
    expires_at: datetime


class RosterSession(BaseModel):
    """A signed-in session, keyed in Firestore by the hash of its token."""

    model_config = DOCUMENT

    squadron_id: str
    person_id: str
    name: str
    role: Role = Role.MEMBER
    expires_at: datetime


class RosterMonth(BaseModel):
    """A month of flying dates, keyed in Firestore by ``{squadronId}:{month}``."""

    model_config = DOCUMENT

    squadron_id: str
    month: str = Field(..., description="Year and month, as 2026-11")
    dates: list[date] = Field(default_factory=list)
    freeze_at: date

    # No frozen flag. It is computed from freeze_at, never stored.
