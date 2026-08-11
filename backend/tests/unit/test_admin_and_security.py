"""
Admin authorization and authentication security tests.

Falsification targets (contract clauses):
- ``require_admin_user``: non-admin -> 403, unknown signed user -> 401,
  admin -> resolves user id.
- ``GET /admin/health``: admin -> 200 {"status": "ok"}; non-admin -> 403;
  unauthenticated -> 401; unknown signed user -> 401.
- ``GET /admin/runtime-settings``: admin -> 200 with settings list;
  non-admin -> 403; unauthenticated -> 401.
- ``GET /tasks/dead-letter/list`` (regression B1): unauthenticated -> 401/403
  (never 200); signed non-admin -> 403; admin -> 200 with
  {"total": int, "tasks": list}.
- ``resolve_authenticated_user_id`` (API key path): valid Bearer/x-api-key
  resolves owning user; unknown key -> 401; revoked key -> 401; API key takes
  precedence over signed session headers; no key falls back to signed headers.
"""

import hashlib
import hmac
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from starlette.requests import Request

from src.auth_headers import hash_api_key, resolve_authenticated_user_id
from src.config import Config
from tests.fixtures.factories import create_user

TEST_SECRET = "test-backend-auth-secret"


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


def _signed_headers(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(
        TEST_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


async def _insert_api_key(db, *, user_id: str, raw_key: str, revoked: bool = False):
    """Insert an api_keys row keyed by the SHA-256 hash of the raw key."""
    key_hash = hash_api_key(raw_key)
    await db.execute(
        text("DELETE FROM api_keys WHERE key_hash = :key_hash"),
        {"key_hash": key_hash},
    )
    await db.execute(
        text(
            """
            INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, created_at)
            VALUES (:id, :user_id, :name, :key_hash, :key_prefix, NOW())
            """
        ),
        {
            "id": str(uuid4()),
            "user_id": user_id,
            "name": "Test Key",
            "key_hash": key_hash,
            "key_prefix": raw_key[:16],
        },
    )
    if revoked:
        await db.execute(
            text("UPDATE api_keys SET revoked_at = NOW() WHERE key_hash = :key_hash"),
            {"key_hash": key_hash},
        )
    await db.commit()


def _test_config() -> Config:
    config = Config()
    config.backend_auth_secret = TEST_SECRET
    config.allow_unsigned_backend_auth = False
    return config


# --- B1 regression: GET /tasks/dead-letter/list ----------------------------


@pytest.mark.asyncio
async def test_dead_letter_list_requires_authentication(client):
    """B1: unauthenticated request must NOT reach the endpoint (was 200)."""
    response = await client.get("/tasks/dead-letter/list")

    assert response.status_code in (401, 403)
    assert response.status_code != 200


@pytest.mark.asyncio
async def test_dead_letter_list_denies_non_admin_user(client, db_session, auth_headers):
    """B1: a signed regular (non-admin) user must be rejected with 403."""
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )

    response = await client.get("/tasks/dead-letter/list", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dead_letter_list_allows_admin_user(client, db_session, auth_headers):
    """B1: an admin user gets 200 with the documented response shape."""
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=True,
    )

    response = await client.get("/tasks/dead-letter/list", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("total"), int)
    assert isinstance(payload.get("tasks"), list)


# --- GET /admin/health -----------------------------------------------------


@pytest.mark.asyncio
async def test_admin_health_allows_admin_user(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=True,
    )

    response = await client.get("/admin/health", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_admin_health_denies_non_admin_user(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )

    response = await client.get("/admin/health", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_health_requires_authentication(client):
    response = await client.get("/admin/health")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_health_rejects_signed_user_not_in_database(client):
    """A validly signed user id that does not exist must not pass the gate."""
    response = await client.get("/admin/health", headers=_signed_headers("ghost-user"))

    assert response.status_code == 401


# --- GET /admin/runtime-settings -------------------------------------------


@pytest.mark.asyncio
async def test_runtime_settings_allows_admin_user(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=True,
    )

    response = await client.get("/admin/runtime-settings", headers=auth_headers)

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert isinstance(settings, list)
    assert all(isinstance(item.get("key"), str) for item in settings)


@pytest.mark.asyncio
async def test_runtime_settings_denies_non_admin_user(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )

    response = await client.get("/admin/runtime-settings", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_runtime_settings_requires_authentication(client):
    response = await client.get("/admin/runtime-settings")

    assert response.status_code == 401


# --- resolve_authenticated_user_id: API key path ---------------------------


@pytest.mark.asyncio
async def test_resolve_user_id_via_bearer_api_key(db_session):
    await create_user(db_session, user_id="user-api", email="api@example.com")
    raw_key = "sk_test_valid_bearer"
    await _insert_api_key(db_session, user_id="user-api", raw_key=raw_key)
    request = _build_request({"authorization": f"Bearer {raw_key}"})

    user_id = await resolve_authenticated_user_id(request, db_session, _test_config())

    assert user_id == "user-api"


@pytest.mark.asyncio
async def test_resolve_user_id_via_x_api_key_header(db_session):
    await create_user(db_session, user_id="user-api", email="api@example.com")
    raw_key = "sk_test_valid_header"
    await _insert_api_key(db_session, user_id="user-api", raw_key=raw_key)
    request = _build_request({"x-api-key": raw_key})

    user_id = await resolve_authenticated_user_id(request, db_session, _test_config())

    assert user_id == "user-api"


@pytest.mark.asyncio
async def test_resolve_user_id_rejects_unknown_api_key(db_session):
    await create_user(db_session, user_id="user-api", email="api@example.com")
    await _insert_api_key(db_session, user_id="user-api", raw_key="sk_test_known")
    request = _build_request({"x-api-key": "sk_unknown_key"})

    with pytest.raises(HTTPException) as exc:
        await resolve_authenticated_user_id(request, db_session, _test_config())

    assert exc.value.status_code == 401
    # The failed lookup leaves an open transaction on the shared session;
    # close it here so the fixture teardown does not roll back on a closed loop.
    await db_session.rollback()


@pytest.mark.asyncio
async def test_resolve_user_id_rejects_revoked_api_key(db_session):
    await create_user(db_session, user_id="user-api", email="api@example.com")
    raw_key = "sk_test_revoked"
    await _insert_api_key(
        db_session, user_id="user-api", raw_key=raw_key, revoked=True
    )
    request = _build_request({"x-api-key": raw_key})

    with pytest.raises(HTTPException) as exc:
        await resolve_authenticated_user_id(request, db_session, _test_config())

    assert exc.value.status_code == 401
    # See test_resolve_user_id_rejects_unknown_api_key.
    await db_session.rollback()


@pytest.mark.asyncio
async def test_resolve_user_id_api_key_takes_precedence_over_signed_headers(db_session):
    """A valid key must win over signed headers claiming a different user."""
    await create_user(db_session, user_id="user-api", email="api@example.com")
    await create_user(db_session, user_id="user-other", email="other@example.com")
    raw_key = "sk_test_precedence"
    await _insert_api_key(db_session, user_id="user-api", raw_key=raw_key)

    headers = _signed_headers("user-other")
    headers["x-api-key"] = raw_key
    request = _build_request(headers)

    user_id = await resolve_authenticated_user_id(request, db_session, _test_config())

    assert user_id == "user-api"


@pytest.mark.asyncio
async def test_resolve_user_id_falls_back_to_signed_headers_without_api_key(db_session):
    request = _build_request(_signed_headers("user-1"))

    user_id = await resolve_authenticated_user_id(request, db_session, _test_config())

    assert user_id == "user-1"
