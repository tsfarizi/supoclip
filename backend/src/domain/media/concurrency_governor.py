"""
Render Concurrency Governor.

Manages render concurrency slots using Redis:
- Uses Redis semaphore / counter key `render_active_count`.
- If active count < max_concurrency, increment counter and return True / slot acquired.
- If active count >= max_concurrency, request enters Redis FIFO list/queue (`render_hold_queue`),
  waits/polls with timeout or pubsub notification until a slot is freed.
- `release_render_slot(redis_client)`: Decrements active counter and publishes notification to wake next queued request.
- Invariant: Maximum active renders <= `render_concurrency` setting; no requests dropped on timeout (returns queued/timeout status cleanly).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import time
from typing import AsyncIterator, Optional, Union
import uuid

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

RENDER_ACTIVE_COUNT_KEY = "render_active_count"
RENDER_HOLD_QUEUE_KEY = "render_hold_queue"
RENDER_SLOT_RELEASED_CHANNEL = "render_slot_released"

# Lua script to atomically acquire a render slot or register in hold queue
ACQUIRE_SLOT_SCRIPT = """
local active_key = KEYS[1]
local queue_key = KEYS[2]
local max_concurrency = tonumber(ARGV[1])
local request_id = ARGV[2]

local current_active = tonumber(redis.call('get', active_key) or '0')
if current_active < 0 then
    current_active = 0
    redis.call('set', active_key, '0')
end

if current_active < max_concurrency then
    redis.call('incr', active_key)
    return {1, current_active + 1}
else
    redis.call('rpush', queue_key, request_id)
    return {0, current_active}
end
"""

# Lua script to try promoting from queue or incrementing slot if available
TRY_POP_QUEUE_SCRIPT = """
local active_key = KEYS[1]
local queue_key = KEYS[2]
local max_concurrency = tonumber(ARGV[1])
local request_id = ARGV[2]

local current_active = tonumber(redis.call('get', active_key) or '0')
if current_active < max_concurrency then
    local next_id = redis.call('lpop', queue_key)
    if next_id == request_id then
        redis.call('incr', active_key)
        return 1
    elseif next_id then
        -- Put it back at the front if it wasn't us
        redis.call('lpush', queue_key, next_id)
        return 0
    else
        -- Queue is empty, can acquire directly
        redis.call('incr', active_key)
        return 1
    end
end
return 0
"""

# Lua script to release a slot
RELEASE_SLOT_SCRIPT = """
local active_key = KEYS[1]
local current_active = tonumber(redis.call('get', active_key) or '0')
if current_active > 0 then
    redis.call('decr', active_key)
    return current_active - 1
else
    redis.call('set', active_key, '0')
    return 0
end
"""


async def acquire_render_slot(
    redis_client: Redis,
    max_concurrency: int,
    timeout: int = 300,
    poll_interval: float = 0.5,
) -> bool:
    """
    Acquires a render slot.
    Returns True if slot acquired within timeout, False otherwise.
    """
    if max_concurrency <= 0:
        max_concurrency = 1

    request_id = str(uuid.uuid4())
    start_time = time.monotonic()

    # Try initial acquire
    try:
        res = await redis_client.eval(
            ACQUIRE_SLOT_SCRIPT,
            2,
            RENDER_ACTIVE_COUNT_KEY,
            RENDER_HOLD_QUEUE_KEY,
            str(max_concurrency),
            request_id,
        )
        acquired = bool(res[0])
        if acquired:
            logger.debug("Acquired render slot immediately (active: %s)", res[1])
            return True
    except Exception as exc:
        logger.warning("Error running acquire script: %s; falling back to direct check", exc)
        curr = int(await redis_client.get(RENDER_ACTIVE_COUNT_KEY) or 0)
        if curr < max_concurrency:
            await redis_client.incr(RENDER_ACTIVE_COUNT_KEY)
            return True
        return False

    # Enter waiting loop
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(RENDER_SLOT_RELEASED_CHANNEL)

    try:
        while time.monotonic() - start_time < timeout:
            # Check if we can get a slot
            try:
                pop_res = await redis_client.eval(
                    TRY_POP_QUEUE_SCRIPT,
                    2,
                    RENDER_ACTIVE_COUNT_KEY,
                    RENDER_HOLD_QUEUE_KEY,
                    str(max_concurrency),
                    request_id,
                )
                if int(pop_res) == 1:
                    logger.debug("Acquired render slot after queuing (req: %s)", request_id)
                    return True
            except Exception as exc:
                logger.debug("Error checking queue: %s", exc)

            # Wait for pubsub message or poll interval
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_interval),
                    timeout=poll_interval,
                )
            except (asyncio.TimeoutError, TimeoutError):
                pass
    finally:
        try:
            await pubsub.unsubscribe(RENDER_SLOT_RELEASED_CHANNEL)
            await pubsub.close()
        except Exception:
            pass
        # If timed out and still in queue, remove request_id from queue
        try:
            await redis_client.lrem(RENDER_HOLD_QUEUE_KEY, 0, request_id)
        except Exception:
            pass

    logger.warning("Render slot acquisition timed out after %ds (req: %s)", timeout, request_id)
    return False


async def release_render_slot(redis_client: Redis) -> None:
    """
    Decrements active counter and publishes notification to wake next queued request.
    """
    try:
        await redis_client.eval(
            RELEASE_SLOT_SCRIPT,
            1,
            RENDER_ACTIVE_COUNT_KEY,
        )
    except Exception as exc:
        logger.warning("Error running release script: %s", exc)
        curr = int(await redis_client.get(RENDER_ACTIVE_COUNT_KEY) or 0)
        if curr > 0:
            await redis_client.decr(RENDER_ACTIVE_COUNT_KEY)
        else:
            await redis_client.set(RENDER_ACTIVE_COUNT_KEY, "0")

    # Publish notification
    try:
        await redis_client.publish(RENDER_SLOT_RELEASED_CHANNEL, "released")
    except Exception as exc:
        logger.debug("Failed to publish render slot released message: %s", exc)


@asynccontextmanager
async def render_slot_guard(
    redis_client: Redis,
    max_concurrency: int,
    timeout: int = 300,
) -> AsyncIterator[bool]:
    """
    Async context manager for acquiring and safely releasing a render slot.
    Yields True if slot acquired, False if timeout.
    """
    acquired = await acquire_render_slot(redis_client, max_concurrency, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            await release_render_slot(redis_client)
