"""Sign-in codes and sessions for the roster."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from app.config import (
    ROSTER_CODE_MAX_ATTEMPTS,
    ROSTER_CODE_REQUESTS_COLLECTION,
    ROSTER_CODE_REQUESTS_PER_HOUR,
    ROSTER_CODE_TTL_MINUTES,
    ROSTER_CODES_COLLECTION,
    ROSTER_PEOPLE_COLLECTION,
    ROSTER_REMEMBER_DEVICE_DAYS,
    ROSTER_SESSION_HOURS,
    ROSTER_SESSIONS_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import RosterCode, RosterPerson, RosterSession
from app.services import database, email_service

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """Raised when a person has asked for too many codes in the last hour."""


def _hash(value: str) -> str:
    """Hash a secret for storage, matching how API keys are stored.

    Args:
        value: Plain text secret.

    Returns:
        Hex SHA-256 digest.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def person_key(person_id: str) -> str:
    """Build the document id for a person in the current squadron.

    Args:
        person_id: STARS person identifier.

    Returns:
        Document id of the form ``{squadronId}:{personId}``.
    """
    # Quoting keeps an id like "a/b" one path segment instead of a 500.
    return f"{STARS_ORG_UNIT_ID}:{quote(person_id, safe='')}"


async def get_person(person_id: str) -> RosterPerson | None:
    """Read a cached squadron member.

    Args:
        person_id: STARS person identifier.

    Returns:
        The person, or None if they are not on the squadron.
    """
    col = database.get_collection(ROSTER_PEOPLE_COLLECTION)
    snapshot = await col.document(person_key(person_id)).get()
    if not snapshot.exists:
        return None
    return RosterPerson.model_validate(snapshot.to_dict())


async def list_people() -> list[RosterPerson]:
    """Read every cached squadron member, in name order.

    Returns:
        The squadron, which is empty until the people cache is seeded.
    """
    col = database.get_collection(ROSTER_PEOPLE_COLLECTION)
    # ponytail: equality filter only, so the automatic single-field index serves it.
    query = col.where(filter=FieldFilter("squadronId", "==", STARS_ORG_UNIT_ID))
    people = [
        RosterPerson.model_validate(doc.to_dict()) async for doc in query.stream()
    ]
    return sorted(people, key=lambda person: person.name)


async def _take_request_allowance(person_id: str, now: datetime) -> None:
    """Consume one of the person's hourly code requests.

    Args:
        person_id: STARS person identifier.
        now: Current UTC time.

    Raises:
        RateLimited: If the hourly allowance is already used up.
    """
    client = database.get_client()
    col = database.get_collection(ROSTER_CODE_REQUESTS_COLLECTION)
    ref = col.document(person_key(person_id))
    cutoff = now - timedelta(hours=1)

    @firestore.async_transactional
    async def take(transaction) -> bool:
        """Prune old timestamps and record this request if there is room."""
        snapshot = await ref.get(transaction=transaction)
        stamps = (
            (snapshot.to_dict() or {}).get("requestedAt", []) if snapshot.exists else []
        )
        recent = [s for s in stamps if datetime.fromisoformat(s) > cutoff]
        if len(recent) >= ROSTER_CODE_REQUESTS_PER_HOUR:
            return False
        recent.append(now.isoformat())
        transaction.set(ref, {"squadronId": STARS_ORG_UNIT_ID, "requestedAt": recent})
        return True

    if not await take(client.transaction()):
        raise RateLimited


async def request_code(person_id: str) -> str:
    """Issue a sign-in code and email it, if the person can receive one.

    Args:
        person_id: STARS person identifier.

    Returns:
        The nonce tying the code to this browser.

    Raises:
        RateLimited: If the person's hourly allowance is used up.
    """
    now = datetime.now(UTC)

    # Before the lookup, so an unknown person is rate limited identically.
    await _take_request_allowance(person_id, now)

    nonce = secrets.token_hex(16)
    person = await get_person(person_id)
    if person is None or not person.email:
        logger.info("Sign-in code requested for unknown or emailless person")
        return nonce

    code = f"{secrets.randbelow(1_000_000):06d}"
    record = RosterCode(
        squadron_id=STARS_ORG_UNIT_ID,
        person_id=person_id,
        code_hash=_hash(code),
        expires_at=now + timedelta(minutes=ROSTER_CODE_TTL_MINUTES),
    )

    col = database.get_collection(ROSTER_CODES_COLLECTION)
    await col.document(_hash(nonce)).set(record.model_dump(by_alias=True, mode="json"))

    # ponytail: sync Resend call makes a known person's request measurably slower.
    try:
        email_service.send_signin_code_email(person.email, person.name, code)
        logger.info("Sign-in code sent for person %s", person_id)
    except email_service.EmailServiceError:
        # The contract is always 202, so a Resend failure must not surface here.
        logger.exception("Sign-in code email failed for person %s", person_id)

    return nonce


async def verify_code(nonce: str, code: str, remember_device: bool) -> dict | None:
    """Check a code and, if it is right, open a session.

    Args:
        nonce: The nonce returned by the code request.
        code: The six-digit code from the email.
        remember_device: Whether to issue a long-lived session.

    Returns:
        The new session including its plain token, or None if the code, the
        nonce, or the state of either was not acceptable.
    """
    now = datetime.now(UTC)
    client = database.get_client()
    ref = database.get_collection(ROSTER_CODES_COLLECTION).document(_hash(nonce))
    code_hash = _hash(code)

    @firestore.async_transactional
    async def consume(transaction) -> dict | None:
        """Spend the code if it is unused, unexpired and correct."""
        snapshot = await ref.get(transaction=transaction)
        if not snapshot.exists:
            return None

        data = snapshot.to_dict() or {}
        attempts = data.get("attempts", 0)
        expires_at = datetime.fromisoformat(data["expiresAt"])
        if (
            data.get("used")
            or expires_at <= now
            or attempts >= ROSTER_CODE_MAX_ATTEMPTS
        ):
            return None

        if not secrets.compare_digest(data.get("codeHash", ""), code_hash):
            transaction.update(ref, {"attempts": attempts + 1})
            return None

        transaction.update(ref, {"used": True})
        return data

    record = await consume(client.transaction())
    if record is None:
        return None

    person = await get_person(record["personId"])
    if person is None:
        return None

    return await _open_session(person, now, remember_device)


async def _open_session(
    person: RosterPerson, now: datetime, remember_device: bool
) -> dict:
    """Mint a session token for a person.

    Args:
        person: The person signing in.
        now: Current UTC time.
        remember_device: Whether to issue a long-lived session.

    Returns:
        The session fields plus the plain token, which is never stored.
    """
    lifetime = (
        timedelta(days=ROSTER_REMEMBER_DEVICE_DAYS)
        if remember_device
        else timedelta(hours=ROSTER_SESSION_HOURS)
    )
    token = secrets.token_urlsafe(32)
    session = RosterSession(
        squadron_id=person.squadron_id,
        person_id=person.person_id,
        name=person.name,
        role=person.role,
        expires_at=now + lifetime,
    )

    col = database.get_collection(ROSTER_SESSIONS_COLLECTION)
    await col.document(_hash(token)).set(session.model_dump(by_alias=True, mode="json"))

    logger.info("Session opened for person %s", person.person_id)
    return {"token": token, **session.model_dump(by_alias=True, mode="json")}


async def resolve_session(token: str) -> dict | None:
    """Look up a live session by its bearer token.

    Args:
        token: The plain session token from the Authorization header.

    Returns:
        The session fields, or None if it is unknown or expired.
    """
    col = database.get_collection(ROSTER_SESSIONS_COLLECTION)
    snapshot = await col.document(_hash(token)).get()
    if not snapshot.exists:
        return None

    data = snapshot.to_dict() or {}
    if datetime.fromisoformat(data["expiresAt"]) <= datetime.now(UTC):
        return None
    return data


async def delete_session(token: str) -> None:
    """Delete one session, leaving the person's others alone.

    Args:
        token: The plain session token from the Authorization header.
    """
    col = database.get_collection(ROSTER_SESSIONS_COLLECTION)
    await col.document(_hash(token)).delete()
