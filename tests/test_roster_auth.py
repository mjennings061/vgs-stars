"""Behavioural tests for roster sign-in, against the Firestore emulator."""

# Fixtures shadow their own names by design in pytest.
# pylint: disable=redefined-outer-name

import os
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.config import (
    DATABASE_USERS_COLLECTION,
    ROSTER_CODES_COLLECTION,
    ROSTER_REMEMBER_DEVICE_DAYS,
    ROSTER_SESSION_HOURS,
    SCOPE_STARS,
)
from app.services import api_keys
from tests.conftest import ADMIN_ID as PERSON_ID
from tests.conftest import HEADERS

pytestmark = [
    pytest.mark.skipif(
        "FIRESTORE_EMULATOR_HOST" not in os.environ,
        reason="needs the Firestore emulator: gcloud emulators firestore start",
    ),
    # Every test here wants the seeded key and people, as the old autouse did.
    pytest.mark.usefixtures("seed"),
]


def _request_code(client, person_id: str = PERSON_ID):
    """Ask for a sign-in code.

    Args:
        client: The test client.
        person_id: Who to request a code for.

    Returns:
        The HTTP response.
    """
    return client.post(
        "/roster/auth/request-code",
        json={"personId": person_id},
        headers=HEADERS,
    )


def _code_from(sent) -> str:
    """Pull the six-digit code out of the email that was sent.

    Args:
        sent: The patched Resend send function.

    Returns:
        The code as it reached the person.
    """
    params = sent.call_args[0][0]
    match = re.search(r"\b\d{6}\b", params["text"])
    assert match, "no six-digit code in the email"
    return match.group()


def test_sign_in_flow(client, sent):
    """Request a code, spend it, read the session back, then close it."""
    response = _request_code(client)
    assert response.status_code == 202
    nonce = response.json()["nonce"]
    assert sent.call_count == 1

    verified = client.post(
        "/roster/auth/verify-code",
        json={"nonce": nonce, "code": _code_from(sent)},
    )
    assert verified.status_code == 200
    body = verified.json()
    assert body["personId"] == PERSON_ID
    assert body["name"] == "J Doe"
    assert body["role"] == "admin"
    assert body["token"]

    auth = {"Authorization": f"Bearer {body['token']}"}
    me = client.get("/roster/auth/me", headers=auth)
    assert me.status_code == 200
    assert me.json() == {
        "personId": body["personId"],
        "name": body["name"],
        "role": body["role"],
        "expiresAt": body["expiresAt"],
    }

    assert client.post("/roster/auth/logout", headers=auth).status_code == 202
    assert client.get("/roster/auth/me", headers=auth).status_code == 401


def test_wrong_code_and_wrong_nonce_are_indistinguishable(client, sent):
    """Neither response may hint at which half of the pair was wrong."""
    nonce = _request_code(client).json()["nonce"]
    real_code = _code_from(sent)

    wrong_code = client.post(
        "/roster/auth/verify-code", json={"nonce": nonce, "code": "000000"}
    )
    wrong_nonce = client.post(
        "/roster/auth/verify-code", json={"nonce": "deadbeef", "code": real_code}
    )

    assert wrong_code.status_code == wrong_nonce.status_code == 401
    assert wrong_code.json() == wrong_nonce.json()


def test_expired_code_is_refused(client, sent, seed):
    """A code past its expiry is no longer acceptable."""
    nonce = _request_code(client).json()["nonce"]
    code = _code_from(sent)

    # Age the code rather than waiting out its lifetime.
    stale = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    for doc in seed.collection(ROSTER_CODES_COLLECTION).stream():
        doc.reference.update({"expiresAt": stale})

    response = client.post(
        "/roster/auth/verify-code", json={"nonce": nonce, "code": code}
    )
    assert response.status_code == 401


def test_code_is_single_use(client, sent):
    """The second use of a good code is refused."""
    nonce = _request_code(client).json()["nonce"]
    code = _code_from(sent)
    body = {"nonce": nonce, "code": code}

    assert client.post("/roster/auth/verify-code", json=body).status_code == 200
    assert client.post("/roster/auth/verify-code", json=body).status_code == 401


def test_code_locks_after_five_attempts(client, sent):
    """Five wrong guesses burn the code, even if the sixth guess is right."""
    nonce = _request_code(client).json()["nonce"]
    code = _code_from(sent)

    for _ in range(5):
        wrong = client.post(
            "/roster/auth/verify-code", json={"nonce": nonce, "code": "000000"}
        )
        assert wrong.status_code == 401

    response = client.post(
        "/roster/auth/verify-code", json={"nonce": nonce, "code": code}
    )
    assert response.status_code == 401


def test_unknown_person_still_gets_a_nonce(client, sent):
    """202 either way, or this endpoint enumerates the squadron."""
    response = _request_code(client, person_id="p-does-not-exist")
    assert response.status_code == 202
    assert response.json()["nonce"]
    assert sent.call_count == 0


def test_a_path_like_person_id_still_gets_a_nonce(client, sent):
    """A slash must not reach Firestore as a document id and 500."""
    response = _request_code(client, person_id="a/b")
    assert response.status_code == 202
    assert response.json()["nonce"]
    assert sent.call_count == 0


def test_a_failed_send_still_gets_a_nonce(client, sent):
    """Resend falling over must not turn 202 into 500."""
    sent.side_effect = RuntimeError("resend is down")
    response = _request_code(client)
    assert response.status_code == 202
    assert response.json()["nonce"]


@pytest.mark.usefixtures("sent")
def test_fourth_code_request_in_an_hour_is_refused(client):
    """Three an hour per person, then 429."""
    for _ in range(3):
        assert _request_code(client).status_code == 202

    assert _request_code(client).status_code == 429


def test_request_code_rejects_a_stars_key(client, seed, sent):
    """A key scoped for STARS must not reach the roster."""
    seed.collection(DATABASE_USERS_COLLECTION).document("stars-key").set(
        {
            "name": "stars",
            "api_key": api_keys.hash_api_key("test-stars-key"),
            "scopes": ["stars"],
        }
    )

    response = client.post(
        "/roster/auth/request-code",
        json={"personId": PERSON_ID},
        headers={"X-API-Key": "test-stars-key"},
    )
    assert response.status_code == 403
    assert sent.call_count == 0


@pytest.mark.parametrize(
    "remember_device,expected_days", [(False, 0), (True, ROSTER_REMEMBER_DEVICE_DAYS)]
)
def test_remember_device_sets_the_session_lifetime(
    client, sent, remember_device, expected_days
):
    """Twelve hours by default, thirty days when the device is remembered."""
    nonce = _request_code(client).json()["nonce"]
    body = client.post(
        "/roster/auth/verify-code",
        json={
            "nonce": nonce,
            "code": _code_from(sent),
            "rememberDevice": remember_device,
        },
    ).json()

    lifetime = datetime.fromisoformat(body["expiresAt"]) - datetime.now(UTC)
    expected = (
        timedelta(days=expected_days)
        if expected_days
        else timedelta(hours=ROSTER_SESSION_HOURS)
    )
    assert expected - timedelta(minutes=1) < lifetime <= expected


def test_stars_key_still_reaches_the_stars_routes(client, seed):
    """The scope check must not lock the existing notification service out."""
    seed.collection(DATABASE_USERS_COLLECTION).document("stars-key").set(
        {
            "name": "661VGS",
            "api_key": api_keys.hash_api_key("test-stars-key"),
            "scopes": [SCOPE_STARS],
        }
    )

    allowed = client.get("/", headers={"X-API-Key": "test-stars-key"})
    assert allowed.status_code == 200

    refused = client.get("/", headers=HEADERS)
    assert refused.status_code == 403


def test_key_with_no_scopes_is_refused(client, seed):
    """Every key must say what it is allowed to do."""
    seed.collection(DATABASE_USERS_COLLECTION).document("legacy-key").set(
        {"name": "legacy", "api_key": api_keys.hash_api_key("legacy-key")}
    )

    response = client.get("/", headers={"X-API-Key": "legacy-key"})
    assert response.status_code == 401
