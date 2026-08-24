from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SETTINGS_CHANGED_CHANNEL = "settings.changed"

_INVALIDATION_RETRY_DELAY_SECONDS = 5.0

RUNTIME_SETTING_KEYS: tuple[str, ...] = (
    "ASSEMBLY_AI_API_KEY",
    "LLM",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_API_KEY",
    "YOUTUBE_DATA_API_KEY",
    "APIFY_API_TOKEN",
    "PEXELS_API_KEY",
)

PROCESS_ENV_SETTING_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_API_KEY",
    }
)

_settings_cache: dict[str, str] = {}
_prefer_admin_value_cache: set[str] = set()
_original_env_values = {key: os.getenv(key) for key in RUNTIME_SETTING_KEYS}
_applied_process_env_keys: set[str] = set()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _get_encryption_secret() -> str:
    secret = (
        os.getenv("APP_SETTINGS_ENCRYPTION_KEY")
        or os.getenv("BACKEND_AUTH_SECRET")
        or os.getenv("BETTER_AUTH_SECRET")
    )
    if not secret or len(secret.strip()) < 16:
        raise RuntimeError(
            "APP_SETTINGS_ENCRYPTION_KEY must be set to save encrypted admin settings "
            "(or provide a strong BACKEND_AUTH_SECRET fallback)."
        )
    return secret.strip()


def _get_aesgcm() -> AESGCM:
    key = hashlib.sha256(_get_encryption_secret().encode("utf-8")).digest()
    return AESGCM(key)


def encrypt_setting_value(value: str) -> str:
    nonce = os.urandom(12)
    encrypted = _get_aesgcm().encrypt(nonce, value.encode("utf-8"), None)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return "v1:" + ":".join(
        [_b64url_encode(nonce), _b64url_encode(tag), _b64url_encode(ciphertext)]
    )


def decrypt_setting_value(encrypted_value: str) -> str:
    parts = encrypted_value.split(":")
    if len(parts) != 4 or parts[0] != "v1":
        raise ValueError("Unsupported encrypted setting format")
    nonce = _b64url_decode(parts[1])
    tag = _b64url_decode(parts[2])
    ciphertext = _b64url_decode(parts[3])
    decrypted = _get_aesgcm().decrypt(nonce, ciphertext + tag, None)
    return decrypted.decode("utf-8")


async def load_runtime_settings_cache(db: AsyncSession) -> None:
    rows = await db.execute(
        text(
            """
            SELECT setting_key, encrypted_value, prefer_admin_value
            FROM app_settings
            WHERE setting_key = ANY(CAST(:setting_keys AS text[]))
            """
        ),
        {"setting_keys": list(RUNTIME_SETTING_KEYS)},
    )

    loaded: dict[str, str] = {}
    prefer_admin_keys: set[str] = set()
    for row in rows.mappings():
        setting_key = row["setting_key"]
        encrypted_value = row["encrypted_value"]
        if bool(row["prefer_admin_value"]):
            prefer_admin_keys.add(setting_key)
        if not encrypted_value:
            continue
        try:
            value = decrypt_setting_value(encrypted_value).strip()
        except Exception as exc:
            logger.warning("Unable to decrypt runtime setting %s: %s", setting_key, exc)
            continue
        if value:
            loaded[setting_key] = value

    _settings_cache.clear()
    _settings_cache.update(loaded)
    _prefer_admin_value_cache.clear()
    _prefer_admin_value_cache.update(prefer_admin_keys)
    logger.info("Loaded %s encrypted runtime settings", len(_settings_cache))


def get_cached_setting(name: str) -> str | None:
    return _settings_cache.get(name)


def setting_prefers_admin(name: str) -> bool:
    return name in _prefer_admin_value_cache


def apply_settings_to_process_env(resolved_settings: Mapping[str, str | None]) -> None:
    # Boundary: this mutates the process-global os.environ for the current
    # process only. Cross-process propagation of admin setting changes is
    # handled by the settings.changed invalidation channel (publish_settings_changed
    # / SettingsInvalidationSubscriber); expanding this function's scope is out
    # of unit P7's contract.
    for key in PROCESS_ENV_SETTING_KEYS:
        value = resolved_settings.get(key)
        if value:
            os.environ[key] = value
            _applied_process_env_keys.add(key)
        elif _original_env_values.get(key):
            os.environ[key] = _original_env_values[key] or ""
        elif key in _applied_process_env_keys:
            os.environ.pop(key, None)
            _applied_process_env_keys.discard(key)


async def get_runtime_setting_rows(db: AsyncSession) -> dict[str, dict[str, object]]:
    rows = await db.execute(
        text(
            """
            SELECT setting_key, encrypted_value, prefer_admin_value, updated_at
            FROM app_settings
            WHERE setting_key = ANY(CAST(:setting_keys AS text[]))
            """
        ),
        {"setting_keys": list(RUNTIME_SETTING_KEYS)},
    )
    return {
        row["setting_key"]: {
            "encrypted_value": row["encrypted_value"],
            "prefer_admin_value": row["prefer_admin_value"],
            "updated_at": row["updated_at"],
        }
        for row in rows.mappings()
    }


def _invalidation_payload() -> str:
    return json.dumps({"published_at": datetime.now(timezone.utc).isoformat()})


async def publish_settings_changed() -> None:
    """Best-effort publish of a settings invalidation signal. Never raises."""
    try:
        # Lazy import keeps this module importable from config.py without a
        # redis_client -> config -> runtime_settings import cycle.
        from ...infra.cache.redis_client import get_redis_client

        await get_redis_client().publish(SETTINGS_CHANGED_CHANNEL, _invalidation_payload())
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "Failed to publish %s invalidation signal; other processes will "
            "not reload until the next settings change",
            SETTINGS_CHANGED_CHANNEL,
            exc_info=True,
        )


class SettingsInvalidationSubscriber:
    """Background listener for the settings.changed channel.

    Each invalidation message triggers a reload of the runtime settings cache
    with a fresh DB session. The listener retries forever, so a transient
    Redis outage only delays the next reload; it never escapes the task.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        try:
            self._task = asyncio.create_task(self._run())
        except Exception:
            logger.exception("Failed to start settings invalidation subscriber")
            self._task = None

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error while stopping settings invalidation subscriber")

    async def _run(self) -> None:
        while True:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Settings invalidation subscription lost (%s); retrying in %ss",
                    exc,
                    _INVALIDATION_RETRY_DELAY_SECONDS,
                )
            try:
                await asyncio.sleep(_INVALIDATION_RETRY_DELAY_SECONDS)
            except asyncio.CancelledError:
                raise

    async def _listen_once(self) -> None:
        # Lazy imports keep this module importable from config.py without a
        # redis_client -> config -> runtime_settings import cycle.
        from ...infra.cache.redis_client import get_redis_client

        redis = get_redis_client()
        pubsub = redis.pubsub()
        await pubsub.subscribe(SETTINGS_CHANGED_CHANNEL)
        logger.info(
            "Settings invalidation subscriber listening on %s", SETTINGS_CHANGED_CHANNEL
        )
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                await self._reload_cache()
        finally:
            try:
                await pubsub.unsubscribe(SETTINGS_CHANGED_CHANNEL)
            except Exception:
                pass
            try:
                await pubsub.close()
            except Exception:
                pass

    @staticmethod
    async def _reload_cache() -> None:
        try:
            # Lazy import: see _listen_once.
            from ...infra.db import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await load_runtime_settings_cache(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to reload runtime settings cache after invalidation: %s", exc
            )
