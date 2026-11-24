"""Seed the roster people cache from STARS.

Run by hand until the nightly sync job (issue #25) takes over. Three STARS
calls: list the unit's ids, fetch their detail, fetch their email addresses.
"""

import sys
from pathlib import Path

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import (  # noqa: E402  # pylint: disable=wrong-import-position
    ROSTER_PEOPLE_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import (  # noqa: E402  # pylint: disable=wrong-import-position
    Role,
    RosterPerson,
)
from app.services import (  # noqa: E402  # pylint: disable=wrong-import-position
    roster_auth,
    stars_client,
)


def seed_people() -> None:
    """Write every person on the squadron into the roster people cache."""
    people = stars_client.get_people_for_unit(STARS_ORG_UNIT_ID)
    if not people:
        print("STARS returned nobody. Refusing to wipe the cache.")
        return

    users = stars_client.get_users([p.user_id for p in people if p.user_id])
    emails = {user.id: user.email for user in users}

    col = firestore.Client().collection(ROSTER_PEOPLE_COLLECTION)

    for person in people:
        record = RosterPerson(
            squadron_id=STARS_ORG_UNIT_ID,
            person_id=person.id,
            name=person.name,
            email=emails.get(person.user_id),
            initials=person.initials,
            rank=person.rank,
            instruct_cat=person.instruct_cat,
        )
        # role is excluded and merge is on, so a rerun keeps admins still in STARS.
        document = record.model_dump(by_alias=True, exclude={"role"})
        col.document(roster_auth.person_key(person.id)).set(document, merge=True)

    # Kept, not deleted, so their answers stay on the grids they filled in.
    seen = {roster_auth.person_key(person.id) for person in people}
    query = col.where(filter=FieldFilter("squadronId", "==", STARS_ORG_UNIT_ID))
    departed = [
        doc.reference
        for doc in query.stream()
        if doc.id not in seen and (doc.to_dict() or {}).get("isActive", True)
    ]
    for reference in departed:
        # Their name stays for old answers; their address and admin rights go.
        reference.update(
            {
                "isActive": False,
                "role": Role.MEMBER.value,
                "email": firestore.DELETE_FIELD,
            }
        )

    print(f"Seeded {len(people)} people into {ROSTER_PEOPLE_COLLECTION}.")
    print(f"Marked {len(departed)} who left the squadron as inactive.")
    print("Now set role: admin on the three admins in the Firestore console.")


if __name__ == "__main__":
    seed_people()
