"""Seed the roster people cache from STARS.

Run by hand until the nightly sync job (issue #25) takes over. Three STARS
calls: list the unit's ids, fetch their detail, fetch their email addresses.
"""

import sys
from pathlib import Path

from google.cloud import firestore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import (  # noqa: E402  # pylint: disable=wrong-import-position
    ROSTER_PEOPLE_COLLECTION,
    STARS_ORG_UNIT_ID,
)
from app.models.roster import (  # noqa: E402  # pylint: disable=wrong-import-position
    RosterPerson,
)
from app.services import (  # noqa: E402  # pylint: disable=wrong-import-position
    roster_auth,
    stars_client,
)


def seed_people() -> None:
    """Write every person on the squadron into the roster people cache."""
    people = stars_client.get_people_for_unit(STARS_ORG_UNIT_ID)
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
        # role is excluded and merge is on, so a rerun cannot demote the admins.
        document = record.model_dump(by_alias=True, exclude={"role"})
        col.document(roster_auth.person_key(person.id)).set(document, merge=True)

    print(f"Seeded {len(people)} people into {ROSTER_PEOPLE_COLLECTION}.")
    print("Now set role: admin on the three admins in the Firestore console.")


if __name__ == "__main__":
    seed_people()
