import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.auth_headers import (
    extract_api_key,
    get_authenticated_user_id,
    get_signed_user_id,
)
from src.config import Config


def _build_request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode("utf-8"), value.encode("utf-8"))
                for key, value in headers.items()
            ],
        }
    )


def test_get_signed_user_id_rejects_missing_headers():
    config = Config()
    config.backend_auth_secret = "secret"

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(_build_request({}), config)

    assert exc.value.status_code == 401


def test_get_signed_user_id_rejects_expired_signature():
    config = Config()
    config.backend_auth_secret = "secret"
    config.auth_signature_ttl_seconds = 1
    request = _build_request(
        {
            "x-supoclip-user-id": "user-1",
            "x-supoclip-ts": str(int(time.time()) - 10),
            "x-supoclip-signature": "invalid",
        }
    )

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(request, config)

    assert exc.value.status_code == 401


def test_get_authenticated_user_id_allows_unsigned_fallback_when_enabled():
    config = Config()
    config.backend_auth_secret = "secret"
    config.allow_unsigned_backend_auth = True

    user_id = get_authenticated_user_id(
        _build_request({"x-supoclip-user-id": "user-1"}),
        config,
    )

    assert user_id == "user-1"


def test_get_authenticated_user_id_keeps_rejecting_bad_signed_headers():
    config = Config()
    config.backend_auth_secret = "secret"
    config.allow_unsigned_backend_auth = True

    with pytest.raises(HTTPException) as exc:
        get_authenticated_user_id(
            _build_request(
                {
                    "x-supoclip-user-id": "user-1",
                    "x-supoclip-ts": str(int(time.time())),
                    "x-supoclip-signature": "invalid",
                }
            ),
            config,
        )

    assert exc.value.status_code == 401


def test_get_authenticated_user_id_accepts_valid_signed_headers():
    config = Config()
    config.backend_auth_secret = "secret"
    timestamp = str(int(time.time()))
    payload = f"user-1:{timestamp}".encode("utf-8")
    signature = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()

    user_id = get_authenticated_user_id(
        _build_request(
            {
                "x-supoclip-user-id": "user-1",
                "x-supoclip-ts": timestamp,
                "x-supoclip-signature": signature,
            }
        ),
        config,
    )

    assert user_id == "user-1"


def _signed_headers(user_id: str, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


# --- get_signed_user_id: valid / boundary cases ---------------------------


def test_get_signed_user_id_accepts_valid_headers():
    config = Config()
    config.backend_auth_secret = "secret"

    user_id = get_signed_user_id(_build_request(_signed_headers("user-1", "secret")), config)

    assert user_id == "user-1"


def test_get_signed_user_id_rejects_invalid_signature():
    """A wrong HMAC signature for a fresh timestamp must be rejected (401)."""
    config = Config()
    config.backend_auth_secret = "secret"
    request = _build_request(
        {
            "x-supoclip-user-id": "user-1",
            "x-supoclip-ts": str(int(time.time())),
            "x-supoclip-signature": "a" * 64,
        }
    )

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(request, config)

    assert exc.value.status_code == 401


def test_get_signed_user_id_rejects_correctly_signed_but_expired_signature():
    """Even a cryptographically valid signature must not bypass the TTL check."""
    config = Config()
    config.backend_auth_secret = "secret"
    config.auth_signature_ttl_seconds = 1
    stale_timestamp = str(int(time.time()) - 10)
    payload = f"user-1:{stale_timestamp}".encode("utf-8")
    signature = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    request = _build_request(
        {
            "x-supoclip-user-id": "user-1",
            "x-supoclip-ts": stale_timestamp,
            "x-supoclip-signature": signature,
        }
    )

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(request, config)

    assert exc.value.status_code == 401


def test_get_signed_user_id_rejects_non_numeric_timestamp():
    config = Config()
    config.backend_auth_secret = "secret"
    request = _build_request(
        {
            "x-supoclip-user-id": "user-1",
            "x-supoclip-ts": "not-a-timestamp",
            "x-supoclip-signature": "a" * 64,
        }
    )

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(request, config)

    assert exc.value.status_code == 401


def test_get_signed_user_id_returns_500_when_secret_not_configured():
    """Without a server secret the endpoint cannot verify anything: 500, never 401."""
    config = Config()
    config.backend_auth_secret = None

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(_build_request(_signed_headers("user-1", "secret")), config)

    assert exc.value.status_code == 500


# --- get_authenticated_user_id: fallback / missing-header boundaries -------


def test_get_authenticated_user_id_rejects_missing_headers_when_unsigned_disallowed():
    config = Config()
    config.backend_auth_secret = "secret"
    config.allow_unsigned_backend_auth = False

    with pytest.raises(HTTPException) as exc:
        get_authenticated_user_id(_build_request({}), config)

    assert exc.value.status_code == 401


def test_get_authenticated_user_id_unsigned_fallback_requires_user_header():
    """Unsigned fallback is allowed but a user id must still be supplied."""
    config = Config()
    config.backend_auth_secret = "secret"
    config.allow_unsigned_backend_auth = True

    with pytest.raises(HTTPException) as exc:
        get_authenticated_user_id(_build_request({}), config)

    assert exc.value.status_code == 401


def test_get_authenticated_user_id_returns_503_when_auth_not_configured():
    config = Config()
    config.backend_auth_secret = None
    config.allow_unsigned_backend_auth = False

    with pytest.raises(HTTPException) as exc:
        get_authenticated_user_id(_build_request({}), config)

    assert exc.value.status_code == 503


# --- extract_api_key: Bearer / x-api-key / absence / precedence ------------


def test_extract_api_key_from_bearer_authorization():
    request = _build_request({"authorization": "Bearer sk_test_abc"})

    assert extract_api_key(request) == "sk_test_abc"


def test_extract_api_key_from_x_api_key_header():
    request = _build_request({"x-api-key": "sk_test_def"})

    assert extract_api_key(request) == "sk_test_def"


def test_extract_api_key_returns_none_when_absent():
    assert extract_api_key(_build_request({})) is None
    assert extract_api_key(_build_request({"x-supoclip-user-id": "user-1"})) is None


def test_extract_api_key_ignores_empty_bearer_token_and_falls_back():
    request = _build_request({"authorization": "Bearer ", "x-api-key": "sk_test_ghi"})

    assert extract_api_key(request) == "sk_test_ghi"


def test_extract_api_key_returns_none_for_blank_bearer_only():
    assert extract_api_key(_build_request({"authorization": "Bearer "})) is None


def test_extract_api_key_bearer_takes_precedence_over_x_api_key():
    request = _build_request(
        {
            "authorization": "Bearer sk_bearer",
            "x-api-key": "sk_header",
        }
    )

    assert extract_api_key(request) == "sk_bearer"
