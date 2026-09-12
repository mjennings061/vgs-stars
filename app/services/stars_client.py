"""STARS API client for retrieving personnel and authorisation data.

This module provides functions to interact with the STARS API,
extracted and enhanced from the exploration notebook.
"""

import logging
from datetime import date
from urllib.parse import quote

import requests

from app.config import STARS_API_KEY, STARS_URI
from app.models.stars import Auth, Person, User

logger = logging.getLogger(__name__)


class StarsAPIError(Exception):
    """Exception raised for STARS API errors."""


def auth_header() -> dict:
    """Construct authorisation headers for STARS API requests.

    Returns:
        Dictionary with Authorization header.
    """
    return {
        "Authorization": STARS_API_KEY,
    }


def get_person(person_id: str) -> Person:
    """Retrieve person information from STARS API.

    Args:
        person_id: STARS resource ID (e.g., "R:125129").

    Returns:
        Person object with personnel details.

    Raises:
        StarsAPIError: If API request fails or person not found.
    """
    url = f"{STARS_URI}/person/personnel"

    logger.debug("Fetching person data for %s", person_id)

    try:
        response = requests.get(
            url, params={"ids": person_id}, headers=auth_header(), timeout=30
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch person %s: %s", person_id, e)
        raise StarsAPIError(f"Failed to fetch person data: {e}") from e

    data = response.json()
    if not data.get("data"):
        raise StarsAPIError(f"Person with ID {person_id} not found")

    person_data = data["data"][0]
    logger.debug("Retrieved person: %s", person_data.get("name"))
    return Person(**person_data)


def get_user(user_id: str) -> User:
    """Retrieve user information from STARS API.

    Args:
        user_id: STARS user ID (UUID format).

    Returns:
        User object with account details.

    Raises:
        StarsAPIError: If API request fails or user not found.
    """
    url = f"{STARS_URI}/user/users/"

    logger.debug("Fetching user data for %s", user_id)

    try:
        response = requests.get(
            url, params={"ids": user_id}, headers=auth_header(), timeout=30
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch user %s: %s", user_id, e)
        raise StarsAPIError(f"Failed to fetch user data: {e}") from e

    data = response.json()
    if not data.get("data"):
        raise StarsAPIError(f"User with ID {user_id} not found")

    user_data = data["data"][0]
    logger.debug("Retrieved user: %s", user_data.get("email"))
    return User(**user_data)


def get_eng_auths_for_user(person_id: str) -> list[Auth]:
    """Retrieve engineering authorisations for a person from STARS API.

    Args:
        person_id: STARS resource ID (e.g., "R:125129").

    Returns:
        List of Auth objects for this person.

    Raises:
        StarsAPIError: If API request fails.
    """
    resource_id = quote(person_id, safe="")
    url = f"{STARS_URI}/eng/personnel/{resource_id}/auths"

    # Get current authorisations
    params = {
        "view": "Current",
        "baseDate": date.today().isoformat(),
    }

    logger.debug("Fetching engineering auths for %s", person_id)

    try:
        response = requests.get(url, params=params, headers=auth_header(), timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch auths for %s: %s", person_id, e)
        raise StarsAPIError(f"Failed to fetch engineering authorisations: {e}") from e

    data = response.json()
    auths_data = data.get("data", [])
    auths = [Auth(**auth) for auth in auths_data]

    logger.debug("Retrieved %d auths for %s", len(auths), person_id)
    return auths


def get_expiring_auths_by_date(unit_id: str, expiry_date: date) -> list[Auth]:
    """Get expiring authorisations before a given date for a unit.

    Args:
        unit_id: Organisation unit ID.
        expiry_date: Date to check expiring auths up to.

    Returns:
        List of Auth objects expiring before the specified date.

    Raises:
        StarsAPIError: If API request fails.
    """
    url = f"{STARS_URI}/eng/personnel/auths"

    params = {
        "view": "Expiring",
        "trade": "",
        "baseDate": expiry_date.isoformat(),
        "orgUnitID": unit_id,
    }

    logger.info("Fetching expiring auths for unit %s before %s", unit_id, expiry_date)

    try:
        response = requests.get(url, params=params, headers=auth_header(), timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch expiring auths: %s", e)
        raise StarsAPIError(f"Failed to fetch expiring auths: {e}") from e

    data = response.json()
    auths_data = data.get("data", [])

    if not auths_data:
        logger.warning("No expiring auths found for unit %s", unit_id)
        return []

    # Unpack auths into the pydantic data format
    auths = [Auth(**auth) for auth in auths_data]
    logger.info("Retrieved %d expiring auths", len(auths))
    return auths


def get_user_from_resource(person_id: str) -> User:
    """Get user information from resource (person) ID.

    Combines person and user lookups into a single convenience function.

    Args:
        person_id: STARS resource ID (e.g., "R:125129").

    Returns:
        User object with account details.

    Raises:
        StarsAPIError: If API request fails.
    """
    logger.debug("Fetching user from resource %s", person_id)

    person = get_person(person_id)
    user = get_user(person.user_id)

    logger.debug("Retrieved user %s from resource %s", user.email, person_id)
    return user


def get_people_for_unit(unit_id: str) -> list[Person]:
    """Retrieve every person on a unit, with the fields the roster needs.

    Args:
        unit_id: Organisation unit ID.

    Returns:
        List of Person objects for the unit, empty if it has nobody on it.

    Raises:
        StarsAPIError: If either API request fails.
    """
    url = f"{STARS_URI}/person/personnel"

    logger.info("Fetching people for unit %s", unit_id)

    try:
        # The parameter is orgUnitID; STARS answers orgUnitId with a 405.
        listing = requests.get(
            url, params={"orgUnitID": unit_id}, headers=auth_header(), timeout=30
        )
        listing.raise_for_status()
        ids = [person["id"] for person in listing.json().get("data", [])]
        if not ids:
            logger.warning("No people found for unit %s", unit_id)
            return []

        # ponytail: one batched call, fine for a squadron, split it past ~500 ids.
        detail = requests.get(
            url, params={"ids": ",".join(ids)}, headers=auth_header(), timeout=60
        )
        detail.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch people for unit %s: %s", unit_id, e)
        raise StarsAPIError(f"Failed to fetch unit personnel: {e}") from e

    people = [Person(**person) for person in detail.json().get("data", [])]
    logger.info("Retrieved %d people for unit %s", len(people), unit_id)
    return people


def get_users(user_ids: list[str]) -> list[User]:
    """Retrieve several user accounts at once, for their email addresses.

    Args:
        user_ids: STARS user IDs (UUID format).

    Returns:
        List of User objects, which may be shorter than the ids asked for.

    Raises:
        StarsAPIError: If the API request fails.
    """
    if not user_ids:
        return []

    url = f"{STARS_URI}/user/users/"

    try:
        response = requests.get(
            url, params={"ids": ",".join(user_ids)}, headers=auth_header(), timeout=60
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch %d users: %s", len(user_ids), e)
        raise StarsAPIError(f"Failed to fetch user data: {e}") from e

    users = [User(**user) for user in response.json().get("data", [])]
    logger.debug("Retrieved %d of %d users", len(users), len(user_ids))
    return users
