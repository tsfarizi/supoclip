"""T8 (P7): settings invalidation propagation falsification.

Contracts pinned by the test plan:
1. ``publish_settings_changed()`` publishes to channel 'settings.changed' with
   a JSON payload carrying ``published_at`` (real Redis, manual pubsub).
2. ``SettingsInvalidationSubscriber``: start subscribes; a publish triggers a
   cache reload that picks up new DB values; stop is clean (pubsub closed,
   background task finished).
3. Without a publish, the cache value stays put - the channel does the work,
   not coincidence.
4. Redis-down guard: with a client pointed at a dead port, start() does not
   crash, the listener enters its retry loop, and stop() is clean.
5. ``apply_settings_to_process_env`` writes PROCESS_ENV_SETTING_KEYS into
   os.environ, replaces old values with new ones, and never leaks between
   calls (the environment is restored to its initial state after each test).

Real-Redis tests run on the session event loop (matching the session-scoped
DB engine pool) and reset the module-level cached Redis client per test to
avoid loop-bound cross-test poisoning. Rows seeded in app_settings are
deleted in teardown.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src import runtime_settings as rs
from src.config import Config, set_config_override
from src.infra import redis_client as redis_client_module


def _uid(prefix: str) -> str:
    from uuid import uuid4

    return f"{prefix}-{uuid4().hex}"[:36]


# ---------------------------------------------------------------------------
# Isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_cached_redis_client():
    """Reset the module-level cached Redis clients before and after each test.

    get_redis_client() caches a client whose connection pool binds to the
    loop active on first use; a client created in one test becomes poisoned
    state for a later test running on a different loop. Nulling per test
    keeps every client loop-bound and independent.
    """
    redis_client_module._async_client = None
    redis_client_module._sync_client = None
    yield
    redis_client_module._async_client = None
    redis_client_module._sync_client = None


@pytest.fixture(autouse=True)
def _encryption_secret_env(monkeypatch):
    """Deterministic encryption secret so encrypt/decrypt works regardless of
    what the surrounding shell exports."""
    monkeypatch.setenv("BACKEND_AUTH_SECRET", "test-backend-auth-secret-2026")
    yield


@pytest.fixture(autouse=True)
def _restore_runtime_module_state():
    """Restore the module-level caches after every test so no value seeded by
    one test leaks into another (runtime settings cache, prefer-admin set,
    and the applied-process-env key set)."""
    cache_snapshot = dict(rs._settings_cache)
    prefer_snapshot = set(rs._prefer_admin_value_cache)
    applied_snapshot = set(rs._applied_process_env_keys)
    yield
    rs._settings_cache.clear()
    rs._settings_cache.update(cache_snapshot)
    rs._prefer_admin_value_cache.clear()
    rs._prefer_admin_value_cache.update(prefer_snapshot)
    rs._applied_process_env_keys.clear()
    rs._applied_process_env_keys.update(applied_snapshot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for(condition, timeout: float = 5.0, interval: float = 0.05):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(interval)


async def _wait_for_subscriber_count(redis, channel: str, expected: int, timeout: float = 5.0):
    async def _count() -> int:
        rows = await redis.pubsub_numsub(channel)
        return rows[0][1] if rows else 0

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        count = await _count()
        if count == expected:
            return
        if loop.time() >= deadline:
            raise AssertionError(
                f"channel {channel!r} subscriber count {count} != {expected}"
            )
        await asyncio.sleep(0.05)


async def _seed_setting(db, key: str, value: str) -> None:
    await db.execute(
        text("DELETE FROM app_settings WHERE setting_key = :k"), {"k": key}
    )
    await db.execute(
        text(
            "INSERT INTO app_settings (setting_key, encrypted_value) "
            "VALUES (:k, :v)"
        ),
        {"k": key, "v": rs.encrypt_setting_value(value)},
    )
    await db.commit()


async def _clear_setting(db, key: str) -> None:
    await db.execute(
        text("DELETE FROM app_settings WHERE setting_key = :k"), {"k": key}
    )
    await db.commit()


# ---------------------------------------------------------------------------
# 1. publish_settings_changed publishes a payload on 'settings.changed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_publish_settings_changed_delivers_payload_on_channel():
    from src.infra.redis_client import get_redis_client

    redis = get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(rs.SETTINGS_CHANGED_CHANNEL)
    try:
        # Drain the subscribe confirmation so the registration is durable
        # before publishing.
        async for msg in pubsub.listen():
            if msg.get("type") == "subscribe":
                break

        await rs.publish_settings_changed()

        async def _receive_message():
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    return msg["data"]

        raw = await asyncio.wait_for(_receive_message(), timeout=5.0)
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        assert "published_at" in payload
        # The timestamp is a parseable ISO-8601 instant.
        datetime.fromisoformat(payload["published_at"])
    finally:
        await pubsub.unsubscribe(rs.SETTINGS_CHANGED_CHANNEL)
        await pubsub.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_publish_settings_changed_never_raises_when_redis_down(monkeypatch):
    """Best-effort contract: a Redis outage must not raise out of the admin
    request path."""
    dead_config = Config()
    dead_config.redis_host = "127.0.0.1"
    dead_config.redis_port = 6399
    set_config_override(dead_config)
    try:
        await rs.publish_settings_changed()  # must not raise
    finally:
        set_config_override(None)


# ---------------------------------------------------------------------------
# 2+3. SettingsInvalidationSubscriber reload on signal; no reload without it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_subscriber_reloads_cache_only_after_publish(
    initialized_database, db_session
):
    """Start -> seed new DB value -> WITHOUT publish the cache keeps the old
    value -> publish -> the cache reloads to the new value -> stop cleanly
    (subscriber count returns to zero)."""
    from src.infra.redis_client import get_redis_client

    key = "OLLAMA_BASE_URL"
    await _seed_setting(db_session, key, "http://first:11434/v1")
    await rs.load_runtime_settings_cache(db_session)
    assert rs.get_cached_setting(key) == "http://first:11434/v1"

    subscriber = rs.SettingsInvalidationSubscriber()
    redis = get_redis_client()
    try:
        await subscriber.start()
        # Wait until the listener's subscription is registered on the channel.
        await _wait_for_subscriber_count(redis, rs.SETTINGS_CHANGED_CHANNEL, 1)

        # Change the DB behind the subscriber's back WITHOUT publishing.
        await db_session.execute(
            text(
                "UPDATE app_settings SET encrypted_value = :v "
                "WHERE setting_key = :k"
            ),
            {"k": key, "v": rs.encrypt_setting_value("http://second:11434/v1")},
        )
        await db_session.commit()
        # Give any hypothetical accidental reload enough time to run; the
        # cache must stay on the old value - the channel does the work.
        await asyncio.sleep(0.3)
        assert rs.get_cached_setting(key) == "http://first:11434/v1"

        # Now the signal: publish and wait for the reload to land.
        await rs.publish_settings_changed()
        await _wait_for(
            lambda: rs.get_cached_setting(key) == "http://second:11434/v1",
            timeout=5.0,
        )

        # Stop cleanly: task finished, pubsub unsubscribed from the channel.
        await subscriber.stop()
        assert subscriber.running is False
        assert subscriber._task is None
        await _wait_for_subscriber_count(redis, rs.SETTINGS_CHANGED_CHANNEL, 0)
    finally:
        await subscriber.stop()
        await _clear_setting(db_session, key)
        await rs.load_runtime_settings_cache(db_session)


# ---------------------------------------------------------------------------
# 4. Redis-down guard: start() never crashes, retry loop runs, stop is clean
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_subscriber_survives_redis_down_with_retry_loop(monkeypatch, caplog):
    """A subscriber pointed at a dead Redis port starts without raising,
    stays alive in its retry loop, and stops cleanly."""
    # Short retry delay via direct construction keeps the test fast and
    # deterministic; the production 5s delay is only a timer constant.
    monkeypatch.setattr(rs, "_INVALIDATION_RETRY_DELAY_SECONDS", 0.05)
    # The OS-level connect timeout is the flaky variable (refused on
    # Windows can take ~2s); pin a short timeout so the first subscribe
    # attempt fails quickly and the retry loop is observed.
    import redis.asyncio as redis_async

    def _dead_client():
        return redis_async.Redis(
            host="127.0.0.1",
            port=6399,
            decode_responses=True,
            socket_connect_timeout=0.3,
        )

    monkeypatch.setattr(redis_client_module, "get_redis_client", _dead_client)

    subscriber = rs.SettingsInvalidationSubscriber()
    try:
        with caplog.at_level(logging.WARNING, logger="src.runtime_settings"):
            await subscriber.start()  # must not raise
            # Give the retry loop time to fail and retry several times.
            await asyncio.sleep(0.6)
            assert subscriber.running is True
            assert "retrying" in caplog.text
        await subscriber.stop()
        assert subscriber.running is False
        assert subscriber._task is None
    finally:
        await subscriber.stop()
        set_config_override(None)


# ---------------------------------------------------------------------------
# 5. apply_settings_to_process_env: process-env propagation
# ---------------------------------------------------------------------------


def test_apply_settings_writes_process_env_keys():
    values = {
        "OPENAI_API_KEY": "sk-new-openai",
        "GOOGLE_API_KEY": "new-google",
        "ANTHROPIC_API_KEY": "new-anthropic",
        "OLLAMA_BASE_URL": "http://new-ollama:11434/v1",
        "OLLAMA_API_KEY": "new-ollama-key",
    }
    try:
        rs.apply_settings_to_process_env(values)
        for key, value in values.items():
            assert os.environ.get(key) == value
    finally:
        for key in values:
            os.environ.pop(key, None)


def test_apply_settings_new_value_replaces_old_value(monkeypatch):
    key = "OLLAMA_BASE_URL"
    monkeypatch.setenv(key, "http://old:11434/v1")
    rs.apply_settings_to_process_env({key: "http://new:11434/v1"})
    assert os.environ.get(key) == "http://new:11434/v1"
    rs.apply_settings_to_process_env({key: "http://newer:11434/v1"})
    assert os.environ.get(key) == "http://newer:11434/v1"


def test_apply_settings_removing_value_restores_original_or_removes_key(monkeypatch):
    key = "OLLAMA_API_KEY"
    original = rs._original_env_values.get(key)
    monkeypatch.setenv(key, "applied-value")
    rs.apply_settings_to_process_env({key: "applied-value"})
    assert os.environ.get(key) == "applied-value"

    # Dropping the value restores the original process env when one existed
    # at import time; otherwise the key is removed entirely.
    rs.apply_settings_to_process_env({key: None})
    if original:
        assert os.environ.get(key) == original
    else:
        assert key not in os.environ


def test_apply_settings_ignores_non_process_env_keys(monkeypatch):
    """Keys outside PROCESS_ENV_SETTING_KEYS (e.g. ASSEMBLY_AI_API_KEY) are
    resolved into config but must NOT be written to os.environ."""
    key = "ASSEMBLY_AI_API_KEY"
    monkeypatch.delenv(key, raising=False)
    rs.apply_settings_to_process_env({key: "should-not-leak"})
    assert key not in os.environ


def test_apply_settings_empty_mapping_changes_nothing():
    snapshot = {key: os.environ.get(key) for key in rs.PROCESS_ENV_SETTING_KEYS}
    rs.apply_settings_to_process_env({})
    for key, value in snapshot.items():
        assert os.environ.get(key) == value