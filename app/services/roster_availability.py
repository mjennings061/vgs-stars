"""Who said what about each flying date, one document per person per month."""

from datetime import UTC, datetime
from urllib.parse import quote

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from app.config import (
    ROSTER_AVAILABILITY_COLLECTION,
    ROSTER_CHANGES_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import (
    ChangeState,
    RosterAvailability,
    RosterChange,
    RosterEntry,
    RosterPending,
    Status,
)
from app.services import database


class PendingExists(Exception):
    """The date already has a change waiting for an admin."""


class ReasonRequired(Exception):
    """A change after the freeze was asked for without saying why."""


class UnknownChange(Exception):
    """No change has that id."""


class NotPending(Exception):
    """The change has already been decided."""


def _key(month: str, person_id: str) -> str:
    """Build the document id for one person's month.

    Args:
        month: Year and month, as 2026-11.
        person_id: STARS person identifier.

    Returns:
        Document id of the form ``{squadronId}:{month}:{personId}``.
    """
    # Quoting keeps an id like "a/b" one path segment instead of a 500.
    return f"{STARS_ORG_UNIT_ID}:{month}:{quote(person_id, safe='')}"


def _dump(model) -> dict:
    """Turn a model into the document Firestore stores.

    Args:
        model: Any roster model.

    Returns:
        The camelCase, JSON-safe document.
    """
    return model.model_dump(by_alias=True, mode="json")


async def read_month(
    month: str, person_ids: list[str]
) -> dict[str, RosterAvailability]:
    """Read a whole month of answers in one round trip.

    Args:
        month: Year and month, as 2026-11.
        person_ids: Everyone whose row the grid needs.

    Returns:
        Each person's month by person id, missing anyone who has answered
        nothing.
    """
    if not person_ids:
        return {}

    col = database.get_collection(ROSTER_AVAILABILITY_COLLECTION)
    # Fetching by id needs no composite index, which the emulator cannot enforce.
    refs = [col.document(_key(month, person_id)) for person_id in person_ids]

    rows = {}
    async for snapshot in database.get_client().get_all(refs):
        if snapshot.exists:
            record = RosterAvailability.model_validate(snapshot.to_dict())
            rows[record.person_id] = record
    return rows


def _write_entry(
    transaction, month: str, person_id: str, day: str, entry: RosterEntry
) -> None:
    """Put an answer on the grid and clear any pending marker for that date.

    Args:
        transaction: The transaction to write in.
        month: Year and month, as 2026-11.
        person_id: Whose row is being written.
        day: The date, as 2026-11-08.
        entry: The answer and who set it.
    """
    ref = database.get_collection(ROSTER_AVAILABILITY_COLLECTION).document(
        _key(month, person_id)
    )
    # Merging writes just this date, so saving two dates at once cannot clash.
    transaction.set(
        ref,
        {
            "squadronId": STARS_ORG_UNIT_ID,
            "month": month,
            "personId": person_id,
            "entries": {day: _dump(entry)},
            "pending": {day: firestore.DELETE_FIELD},
        },
        merge=True,
    )


async def save(
    month: str,
    person_id: str,
    day: str,
    entry: RosterEntry,
    *,
    needs_approval: bool,
) -> str | None:
    """Write one person's answer, or ask an admin to approve it.

    A change of answer that needs approval becomes a pending change. A blank
    being filled, or only the comment changing, lands straight away. A write
    that lands closes any pending change on that date as superseded.

    Args:
        month: Year and month, as 2026-11.
        person_id: Whose row is being written.
        day: The date, as 2026-11-08.
        entry: The answer and who set it.
        needs_approval: True for a member writing a frozen date.

    Returns:
        The pending change's id, or None if the answer was written.

    Raises:
        PendingExists: If approval is needed and a change is already waiting.
        ReasonRequired: If a pending change would carry no comment.
    """
    client = database.get_client()
    ref = database.get_collection(ROSTER_AVAILABILITY_COLLECTION).document(
        _key(month, person_id)
    )
    changes = database.get_collection(ROSTER_CHANGES_COLLECTION)

    def change(state: ChangeState, from_status: Status | None = None) -> RosterChange:
        """Log this write in the given state."""
        return RosterChange(
            squadron_id=STARS_ORG_UNIT_ID,
            month=month,
            person_id=person_id,
            date=day,
            from_status=from_status,
            to_status=entry.status,
            reason=entry.comment,
            state=state,
            updated_by=entry.updated_by,
            updated_by_name=entry.updated_by_name,
            updated_at=entry.updated_at,
        )

    @firestore.async_transactional
    async def write(transaction) -> str | None:
        """Decide, inside one transaction, whether the answer lands."""
        snapshot = await ref.get(transaction=transaction)
        record = (
            RosterAvailability.model_validate(snapshot.to_dict())
            if snapshot.exists
            else None
        )
        current = record.entries.get(day) if record else None
        pending = record.pending.get(day) if record else None

        if needs_approval:
            if pending:
                raise PendingExists
            if current and current.status != entry.status:
                if not entry.comment:
                    raise ReasonRequired
                change_ref = changes.document()
                transaction.set(
                    change_ref, _dump(change(ChangeState.PENDING, current.status))
                )
                transaction.set(
                    ref,
                    {
                        "pending": {
                            day: _dump(
                                RosterPending(
                                    change_id=change_ref.id,
                                    to_status=entry.status,
                                    reason=entry.comment,
                                )
                            )
                        }
                    },
                    merge=True,
                )
                return change_ref.id

        if pending:
            transaction.update(
                changes.document(pending.change_id),
                {
                    "state": ChangeState.SUPERSEDED.value,
                    "decidedBy": entry.updated_by,
                    "decidedByName": entry.updated_by_name,
                    "decidedAt": entry.updated_at.isoformat(),
                },
            )
        _write_entry(transaction, month, person_id, day, entry)
        # Appended, never updated, so an overwritten answer stays on the record.
        transaction.set(changes.document(), _dump(change(ChangeState.APPROVED)))
        return None

    return await write(client.transaction())


async def decide(
    change_id: str,
    *,
    approve: bool,
    decided_by: str,
    decided_by_name: str,
    comment: str | None = None,
) -> None:
    """Approve or reject a pending change, once.

    Args:
        change_id: The change to decide.
        approve: True to write the new answer, False to leave the old one.
        decided_by: The admin deciding.
        decided_by_name: Their name.
        comment: The admin's reason, sent to the person on a rejection.

    Raises:
        UnknownChange: If there is no such change.
        NotPending: If it has already been decided.
    """
    client = database.get_client()
    ref = database.get_collection(ROSTER_CHANGES_COLLECTION).document(change_id)
    now = datetime.now(UTC)

    @firestore.async_transactional
    async def apply(transaction) -> None:
        """Check it is still pending and close it, so only one admin wins."""
        snapshot = await ref.get(transaction=transaction)
        if not snapshot.exists:
            raise UnknownChange
        record = RosterChange.model_validate(snapshot.to_dict())
        if record.state != ChangeState.PENDING:
            raise NotPending

        day = record.date.isoformat()
        if approve:
            entry = RosterEntry(
                status=record.to_status,
                comment=record.reason,
                updated_by=record.updated_by,
                updated_by_name=record.updated_by_name,
                updated_at=now,
            )
            _write_entry(transaction, record.month, record.person_id, day, entry)
        else:
            transaction.set(
                database.get_collection(ROSTER_AVAILABILITY_COLLECTION).document(
                    _key(record.month, record.person_id)
                ),
                {"pending": {day: firestore.DELETE_FIELD}},
                merge=True,
            )

        state = ChangeState.APPROVED if approve else ChangeState.REJECTED
        transaction.update(
            ref,
            {
                "state": state.value,
                "decidedBy": decided_by,
                "decidedByName": decided_by_name,
                "decidedAt": now.isoformat(),
                "decisionComment": comment,
            },
        )

    await apply(client.transaction())


async def list_changes(
    state: ChangeState | None = None,
) -> list[tuple[str, RosterChange]]:
    """List logged changes across every grid, newest first.

    Args:
        state: Only changes in this state, or every change.

    Returns:
        Each change with its id.
    """
    query = database.get_collection(ROSTER_CHANGES_COLLECTION).where(
        filter=FieldFilter("squadronId", "==", STARS_ORG_UNIT_ID)
    )
    if state is not None:
        query = query.where(filter=FieldFilter("state", "==", state.value))

    # ponytail: sorted here to avoid a composite index; page it if the log gets big.
    rows = [
        (doc.id, RosterChange.model_validate(doc.to_dict()))
        async for doc in query.stream()
    ]
    return sorted(rows, key=lambda row: row[1].updated_at, reverse=True)
