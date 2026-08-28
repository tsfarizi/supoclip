import asyncio
import pytest

from src.domain.media.concurrency_governor import (
    RENDER_ACTIVE_COUNT_KEY,
    RENDER_HOLD_QUEUE_KEY,
    acquire_render_slot,
    release_render_slot,
    render_slot_guard,
)


class MockRedis:
    def __init__(self):
        self.data = {}
        self.lists = {}
        self.subscribers = []

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, val):
        self.data[key] = str(val)

    async def incr(self, key):
        curr = int(self.data.get(key, 0))
        self.data[key] = str(curr + 1)
        return curr + 1

    async def decr(self, key):
        curr = int(self.data.get(key, 0))
        new_val = max(0, curr - 1)
        self.data[key] = str(new_val)
        return new_val

    async def eval(self, script, numkeys, *args):
        # Emulate the Lua script logic in Python for mock testing
        active_key = args[0]
        if "max_concurrency" in script or "current_active < max_concurrency" in script:
            max_concurrency = int(args[2])
            req_id = args[3]
            curr = int(self.data.get(active_key, 0))
            if "TRY_POP_QUEUE" in script or "lpop" in script:
                if curr < max_concurrency:
                    queue = self.lists.get(args[1], [])
                    if queue and queue[0] == req_id:
                        queue.pop(0)
                        self.data[active_key] = str(curr + 1)
                        return 1
                    elif not queue:
                        self.data[active_key] = str(curr + 1)
                        return 1
                return 0
            else:
                if curr < max_concurrency:
                    self.data[active_key] = str(curr + 1)
                    return [1, curr + 1]
                else:
                    self.lists.setdefault(args[1], []).append(req_id)
                    return [0, curr]
        elif "RELEASE_SLOT" in script or "decr" in script:
            curr = int(self.data.get(active_key, 0))
            if curr > 0:
                self.data[active_key] = str(curr - 1)
                return curr - 1
            else:
                self.data[active_key] = "0"
                return 0
        return 0

    async def publish(self, channel, msg):
        return 1

    async def lrem(self, key, count, value):
        if key in self.lists:
            self.lists[key] = [x for x in self.lists[key] if x != value]

    def pubsub(self):
        return MockPubSub(self)


class MockPubSub:
    def __init__(self, redis):
        self.redis = redis

    async def subscribe(self, channel):
        pass

    async def unsubscribe(self, channel):
        pass

    async def close(self):
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=0.1):
        await asyncio.sleep(0.01)
        return None


@pytest.mark.asyncio
async def test_acquire_and_release_slots():
    redis = MockRedis()

    # Slot 1 acquired
    res1 = await acquire_render_slot(redis, max_concurrency=2, timeout=1)
    assert res1 is True
    assert int(redis.data[RENDER_ACTIVE_COUNT_KEY]) == 1

    # Slot 2 acquired
    res2 = await acquire_render_slot(redis, max_concurrency=2, timeout=1)
    assert res2 is True
    assert int(redis.data[RENDER_ACTIVE_COUNT_KEY]) == 2

    # Slot 3 should be queued & time out since concurrency is 2
    res3 = await acquire_render_slot(redis, max_concurrency=2, timeout=0.1, poll_interval=0.02)
    assert res3 is False

    # Release one slot
    await release_render_slot(redis)
    assert int(redis.data[RENDER_ACTIVE_COUNT_KEY]) == 1

    # Now acquire should succeed
    res4 = await acquire_render_slot(redis, max_concurrency=2, timeout=1)
    assert res4 is True
    assert int(redis.data[RENDER_ACTIVE_COUNT_KEY]) == 2


@pytest.mark.asyncio
async def test_render_slot_guard():
    redis = MockRedis()

    async with render_slot_guard(redis, max_concurrency=1, timeout=1) as acquired:
        assert acquired is True
        assert int(redis.data[RENDER_ACTIVE_COUNT_KEY]) == 1

    # Slot automatically released upon exiting context
    assert int(redis.data[RENDER_ACTIVE_COUNT_KEY]) == 0
