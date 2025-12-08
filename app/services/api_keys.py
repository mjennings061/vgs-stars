"""API key storage helpers for MongoDB-backed authentication."""

import hashlib

from app.config import get_settings
from app.services import database


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA-256 for storage and comparison.

    Args:
        api_key (str): API key in text to be hashed

    Returns:
        str: Hashed API key"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def resolve_api_key(api_key: str) -> dict | None:
    """Look up a hashed API key record.

    Args:
        api_key (str): Hashed API key to be checked

    Returns:
        dict: Record of API key or None if none found"""
    settings = get_settings()
    col = database.get_collection(settings.mongo.users_collection)

    key_hash = hash_api_key(api_key)
    record = col.find_one({"api_key": key_hash})

    return record
