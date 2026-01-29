"""API key storage helpers for Firestore-backed authentication."""

import hashlib

from google.cloud.firestore_v1 import FieldFilter

from app.config import get_settings
from app.services import database


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA-256 for storage and comparison.

    Args:
        api_key (str): API key in text to be hashed

    Returns:
        str: Hashed API key"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def resolve_api_key(api_key: str) -> dict | None:
    """Look up a hashed API key record.

    Args:
        api_key (str): Hashed API key to be checked

    Returns:
        dict: Record of API key or None if none found"""

    settings = get_settings()
    col = database.get_collection(settings.database.users_collection)

    key_hash = hash_api_key(api_key)
    query = col.where(filter=FieldFilter("api_key", "==", key_hash)).limit(1)

    async for doc in query.stream():
        data = doc.to_dict()
        if data:
            data["_id"] = doc.id
        return data

    return None
