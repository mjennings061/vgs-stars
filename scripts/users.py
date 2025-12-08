"""CLI to create users with hashed API keys in MongoDB."""

import secrets

import click

from app.config import get_settings
from app.models.user import ApiUser
from app.services import api_keys, database


@click.command()
@click.option("--name", prompt="User name", help="Name for the API user")
def create_user(name: str) -> None:
    """Generate (or replace) a user API key and print the plaintext key."""
    settings = get_settings()
    database.get_client()
    database.ensure_indexes()

    key_plain = secrets.token_urlsafe(32)
    key_hash = api_keys.hash_api_key(key_plain)

    user = ApiUser(name=name, api_key=key_hash)

    col = database.get_collection(settings.mongo.users_collection)

    result = col.replace_one(
        {"name": name},
        user.model_dump(by_alias=True),
        upsert=True,
    )

    action = "updated" if result.matched_count else "created"
    click.echo(
        f"\nUser {action}. Store this API key securely; it will not be shown again.\n"
    )
    click.echo(f"User   : {name}")
    click.echo(f"Header : {settings.app.api_key_header_name}")
    click.echo(f"API key: {key_plain}\n")
