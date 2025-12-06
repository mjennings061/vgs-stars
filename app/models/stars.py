"""Pydantic models for STARS API responses.

These models represent the data structures returned by the STARS API,
providing type safety and validation for personnel, user, and authorisation data.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ResourceType(BaseModel):
    """Resource type information."""

    id: str
    name: str
    index: int | None = None
    level: int | None = None


class Person(BaseModel):
    """Person/personnel data from STARS API.

    Represents a person in the STARS system with their role, rank,
    and organisation details.
    """

    id: str
    name: str
    # Some STARS records omit these fields, so keep them optional to avoid
    # validation failures.
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    display_name: str | None = Field(default=None, alias="displayName")
    user_id: str = Field(alias="userId")
    resource_type_id: str = Field(alias="resourceTypeId")
    resource_type: str = Field(alias="resourceType")
    resource_type_lineage: list[ResourceType] | None = Field(
        default=None, alias="resourceTypeLineage"
    )
    org_unit_id: int = Field(alias="orgUnitId")
    org_unit: str = Field(alias="orgUnit")
    loan_org_unit_id: int | None = Field(default=None, alias="loanOrgUnitId")
    loan_org_unit: str | None = Field(default=None, alias="loanOrgUnit")
    start_date: datetime | None = Field(default=None, alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    service_number: str | None = Field(default=None, alias="serviceNumber")
    service: str | None = None
    initials: str | None = None
    known_as: str | None = Field(default=None, alias="knownAs")
    birth_date: datetime | None = Field(default=None, alias="birthDate")
    gender: str | None = None
    post: str | None = None
    rank_id: int | None = Field(default=None, alias="rankId")
    rank: str | None = None
    rank_order: int | None = Field(default=None, alias="rankOrder")
    instruct_cat: str | None = Field(default=None, alias="instructCat")
    callsign: str | None = None
    ability: str | None = None
    tier: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class User(BaseModel):
    """User data from STARS API.

    Represents a user account with authentication and status information.
    """

    id: str
    name: str
    email: str
    status: str
    base_status: str = Field(alias="baseStatus")
    created_date: datetime | None = Field(default=None, alias="createdDate")
    activated_date: datetime | None = Field(default=None, alias="activatedDate")
    template: str | None = None
    two_factor_auth: bool | None = Field(default=None, alias="twoFactorAuth")
    two_factor_auth_type: str | None = Field(default=None, alias="twoFactorAuthType")
    two_factor_auth_configured: bool | None = Field(
        default=None, alias="twoFactorAuthConfigured"
    )

    model_config = ConfigDict(populate_by_name=True)


class Auth(BaseModel):
    """Engineering authorisation from STARS API.

    Represents an authorisation with expiry, state, and currency information.
    """

    id: int
    map_id: int = Field(alias="mapId")
    map_name: str = Field(alias="mapName")
    state: str
    currency_state: str = Field(alias="currencyState")
    map_level: str = Field(alias="mapLevel")
    notes: str | None = None
    resource_id: str = Field(alias="resourceId")
    resource_name: str = Field(alias="resourceName")
    resource_rank: str | None = Field(default=None, alias="resourceRank")
    org_unit_id: int = Field(alias="orgUnitId")
    org_unit: str = Field(alias="orgUnit")
    completed: date | None = None
    expiry: date | None = None
    resource_types: list[ResourceType] | None = Field(
        default=None, alias="resourceTypes"
    )
    actions: list[str] | None = None
    proposed_assess_resource_id: str | None = Field(
        default=None, alias="proposedAssessResourceId"
    )
    proposed_assess_resource_name: str | None = Field(
        default=None, alias="proposedAssessResourceName"
    )
    assessed_resource_id: str | None = Field(default=None, alias="assessedResourceId")
    assessed_resource_name: str | None = Field(
        default=None, alias="assessedResourceName"
    )
    proposed_auth_resource_id: str | None = Field(
        default=None, alias="proposedAuthResourceId"
    )
    proposed_auth_resource_name: str | None = Field(
        default=None, alias="proposedAuthResourceName"
    )
    authorised_resource_id: str | None = Field(
        default=None, alias="authorisedResourceId"
    )
    authorised_resource_name: str | None = Field(
        default=None, alias="authorisedResourceName"
    )
    assess_clearance: bool | None = Field(default=None, alias="assessClearance")
    archived: bool | None = None
    archive_reason: str | None = Field(default=None, alias="archiveReason")
    cofc: bool | None = None
    base_date: datetime | None = Field(default=None, alias="baseDate")
    concurrency_id: int | None = Field(default=None, alias="concurrencyId")

    model_config = ConfigDict(populate_by_name=True)


class AuthGroup(BaseModel):
    """Grouped authorisations by person with user information.

    Combines user details with their list of authorisations.
    """

    user: User
    auths: list[Auth]
