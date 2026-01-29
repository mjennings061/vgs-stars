"""CLI to create users with hashed API keys in Firestore."""

import secrets
import sys
from pathlib import Path

import click
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import (  # noqa: E402  # pylint: disable=wrong-import-position
    get_settings,
)
from app.models.user import (  # noqa: E402  # pylint: disable=wrong-import-position
    ApiUser,
)
from app.services import (  # noqa: E402  # pylint: disable=wrong-import-position
    api_keys,
)


@click.command()
@click.option("--name", prompt="User name", help="Name for the API user")
def create_user(name: str) -> None:
    """Generate (or replace) a user API key and print the plaintext key."""

    settings = get_settings()
    # Use synchronous client for CLI script
    client = firestore.Client()

    key_plain = secrets.token_urlsafe(32)
    key_hash = api_keys.hash_api_key(key_plain)

    user = ApiUser(name=name, api_key=key_hash)

    col = client.collection(settings.database.users_collection)

    # Check if user already exists
    query = col.where(filter=FieldFilter("name", "==", name)).limit(1)
    existing_docs = list(query.stream())

    if existing_docs:
        # Update existing user
        doc = existing_docs[0]
        doc.reference.set(user.model_dump(by_alias=True))
        action = "updated"
    else:
        # Create new user
        col.document().set(user.model_dump(by_alias=True))
        action = "created"

    click.echo(
        f"\nUser {action}. Store this API key securely; "
        "it will not be shown again.\n"
    )
    click.echo(f"User   : {name}")
    click.echo(f"Header : {settings.app.api_key_header_name}")
    click.echo(f"API key: {key_plain}\n")


if __name__ == "__main__":
    create_user()  # pylint: disable=no-value-for-parameter
