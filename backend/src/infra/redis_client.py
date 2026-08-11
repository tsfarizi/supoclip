"""Redis client factory: cached async and sync clients built from Config."""

from typing import Optional

import redis as redis_sync
import redis.asyncio as redis_async

from ..config import get_config

_async_client: Optional[redis_async.Redis] = None
_sync_client: Optional[redis_sync.Redis] = None


def _connection_kwargs() -> dict:
    config = get_config()
    return {
        "host": config.redis_host,
        "port": config.redis_port,
        "password": config.redis_password,
        "decode_responses": True,
    }


def get_redis_client() -> redis_async.Redis:
    """Return the cached async Redis client, creating it on first call."""
    global _async_client
    if _async_client is None:
        _async_client = redis_async.Redis(**_connection_kwargs())
    return _async_client


def get_sync_redis_client() -> redis_sync.Redis:
    """Return the cached sync Redis client, creating it on first call."""
    global _sync_client
    if _sync_client is None:
        _sync_client = redis_sync.Redis(**_connection_kwargs())
    return _sync_client


async def close_redis_clients() -> None:
    """Close both cached clients and reset the cache. Never raises."""
    global _async_client, _sync_client
    client = _async_client
    _async_client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass

    client = _sync_client
    _sync_client = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
