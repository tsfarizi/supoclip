"""Regression tests for persisted sound-effects-count propagation.

The queue and Redis boundaries are deterministic doubles.  These tests never
invoke a provider, downloader, encoder, or Freesound endpoint.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import tasks as tasks_route


# Captured create_task_with_source kwargs: proves the tasks-column persistence
# boundary of the create route.
created = {}


class _Request:
    def __init__(self, payload=None):
        self._payload = payload or {}
        self.app = SimpleNamespace(state=SimpleNamespace())

    async def json(self):
        return self._payload


class _CreateTaskService:
    def __init__(self, _db):
        self.video_service = SimpleNamespace(
            determine_source_type=lambda _url: "youtube"
        )
        self.task_repo = SimpleNamespace()

    async def find_active_task_for_source(self, _url):
        return None

    async def create_task_with_source(self, **kwargs):
        created.update(kwargs)
        return "task-created"


class _BillingService:
    def __init__(self, _db):
        pass

    async def assert_can_create_task(self, _user_id):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(6))
async def test_create_passes_each_valid_sfx_count_to_persistence_and_enqueue(
    monkeypatch, count
):
    """Create contract: the API-facing 0..5 is persisted (create_task_with_source
    column args) and forwarded unchanged to the worker job args."""
    request = _Request({"source": {"url": "https://youtu.be/demo"}, "sound_effects_count": count})
    queued = {}
    created.clear()

    class _Queue:
        @staticmethod
        async def enqueue_processing_job(name, mode, *args, **kwargs):
            queued.update(name=name, mode=mode, args=args, kwargs=kwargs)
            return "job-created"

    request.app.state.queue_adapter = _Queue
    monkeypatch.setattr(tasks_route, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(tasks_route, "BillingService", _BillingService)
    monkeypatch.setattr(tasks_route, "TaskService", _CreateTaskService)

    await tasks_route.create_task(request, object())

    assert queued["name"] == "process_video_task"
    assert queued["kwargs"]["sound_effects_count"] == count
    # The tasks row (created via create_task_with_source) persists the count.
    assert created["sound_effects_count"] == count


class _ResumeRedis:
    async def delete(self, _key):
        return 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(6))
async def test_resume_passes_persisted_sfx_count_to_worker_job(monkeypatch, count):
    """Resume contract: the task row's persisted 0..5 reaches the worker job.
    The settings live in the tasks columns only; there is no metadata cache to
    refresh."""
    task = {
        "id": "task-resume",
        "user_id": "user-1",
        "status": "error",
        "source_url": "https://youtu.be/demo",
        "source_type": "youtube",
        "sound_effects_count": count,
        "processing_mode": "fast",
        "output_format": "vertical",
        "add_subtitles": True,
        "hook_persist": False,
        "watermark": None,
        "watermark_persist": False,
        "cleanup_settings_json": {},
    }
    request = _Request()
    queued = {}

    class _TaskService:
        def __init__(self, _db):
            self.task_repo = SimpleNamespace(
                get_task_by_id=AsyncMock(return_value=task),
                update_task_status=AsyncMock(),
            )

    class _Queue:
        @staticmethod
        async def enqueue_processing_job(name, mode, *args, **kwargs):
            queued.update(name=name, mode=mode, args=args, kwargs=kwargs)
            return "job-resumed"

    monkeypatch.setattr(tasks_route, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(tasks_route, "TaskService", _TaskService)
    monkeypatch.setattr(tasks_route, "JobQueue", _Queue)
    monkeypatch.setattr(tasks_route, "get_redis_client", lambda: _ResumeRedis())
    monkeypatch.setattr(tasks_route, "get_config", lambda: SimpleNamespace(default_processing_mode="fast"))

    await tasks_route.resume_task("task-resume", request, object())

    assert queued["name"] == "process_video_task"
    assert queued["kwargs"]["sound_effects_count"] == count


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(6))
async def test_worker_forwards_each_sfx_count_to_process_task(monkeypatch, count):
    """Worker contract: process_video_task forwards the bounded count unchanged."""
    from src.workers import tasks as worker_tasks

    forwarded = {}

    class _Db:
        async def rollback(self):
            pass

    class _Session:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *_args):
            return False

    class _Service:
        def __init__(self, _db):
            pass

        async def process_task(self, **kwargs):
            forwarded.update(kwargs)
            return {"ok": True}

    class _Redis:
        async def get(self, _key):
            return None

    monkeypatch.setattr(worker_tasks, "AsyncSessionLocal", lambda: _Session(), raising=False)
    monkeypatch.setattr(worker_tasks, "load_runtime_settings_cache", AsyncMock(), raising=False)
    monkeypatch.setattr(worker_tasks, "TaskService", _Service, raising=False)

    # These imports are local to process_video_task, so patch their owning modules.
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr("src.runtime_settings.load_runtime_settings_cache", AsyncMock())
    monkeypatch.setattr("src.services.task_service.TaskService", _Service)

    await worker_tasks.process_video_task(
        {"redis": _Redis()}, "task", "upload://demo", "upload", "user", sound_effects_count=count
    )

    assert forwarded["sound_effects_count"] == count
