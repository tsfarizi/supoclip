import json

import pytest

from src.api.routes.tasks import _merge_task_source_metadata
from src.services.task_service import TaskService


class _FailingRedisClient:
    async def get(self, _key: str):
        raise RuntimeError("redis unavailable")

    async def close(self):
        return None


class _RedisClient:
    def __init__(self, payload: str):
        self.payload = payload

    async def get(self, _key: str):
        return self.payload

    async def close(self):
        return None


def _legacy_task_dict(task_id: str = "task-123") -> dict:
    """A task row whose Schema v2 columns are all NULL (a legacy row).

    _load_task_source_settings takes the task dict returned by
    get_task_by_id; a NULL column marks that part of the record as still
    dependent on the legacy task_source:{task_id} Redis cache.
    """
    return {
        "id": task_id,
        "output_format": None,
        "add_subtitles": None,
        "cleanup_settings_json": None,
    }


def _default_settings() -> dict:
    return {
        "output_format": "vertical",
        "add_subtitles": True,
        "cut_long_pauses": False,
        "pause_threshold_ms": 900,
        "remove_filler_words": False,
        "filtered_words": [],
    }


@pytest.mark.asyncio
async def test_load_task_source_settings_falls_back_when_redis_fails(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: _FailingRedisClient(),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(_legacy_task_dict())

    assert settings["output_format"] == "vertical"
    assert settings["add_subtitles"] is True
    assert settings["cut_long_pauses"] is False
    assert settings["pause_threshold_ms"] == 900
    assert settings["remove_filler_words"] is False
    assert settings["filtered_words"] == []


@pytest.mark.asyncio
async def test_load_task_source_settings_uses_db_columns_without_touching_redis(
    monkeypatch,
):
    # DB-first contract: populated Schema v2 columns are authoritative and the
    # legacy Redis cache must not even be consulted.
    def _redis_must_not_be_used(**_kwargs):
        raise AssertionError("Redis must not be touched when DB columns are populated")

    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client", _redis_must_not_be_used
    )

    task = {
        "id": "task-123",
        "output_format": "original",
        "add_subtitles": False,
        # asyncpg 0.31 decodes the jsonb column to a dict natively.
        "cleanup_settings_json": {
            "cut_long_pauses": True,
            "pause_threshold_ms": 1400,
            "remove_filler_words": True,
            "filtered_words": ["basically", "like"],
        },
    }

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(task)

    assert settings == {
        "output_format": "original",
        "add_subtitles": False,
        "cut_long_pauses": True,
        "pause_threshold_ms": 1400,
        "remove_filler_words": True,
        "filtered_words": ["basically", "like"],
    }


@pytest.mark.asyncio
async def test_load_task_source_settings_falls_back_per_column(monkeypatch):
    # A row with only output_format populated still reads add_subtitles and the
    # cleanup settings from the legacy Redis payload.
    payload = json.dumps(
        {
            "output_format": "vertical_split",  # ignored: DB column wins
            "add_subtitles": False,
            "cut_long_pauses": True,
            "pause_threshold_ms": 1100,
            "remove_filler_words": True,
            "filtered_words": ["um"],
        }
    )
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: _RedisClient(payload),
    )

    task = {
        "id": "task-123",
        "output_format": "original",
        "add_subtitles": None,
        "cleanup_settings_json": None,
    }

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(task)

    assert settings["output_format"] == "original"  # DB column wins
    assert settings["add_subtitles"] is False  # Redis fallback
    assert settings["cut_long_pauses"] is True
    assert settings["pause_threshold_ms"] == 1100
    assert settings["remove_filler_words"] is True
    assert settings["filtered_words"] == ["um"]


@pytest.mark.asyncio
async def test_load_task_source_settings_sanitizes_invalid_db_columns(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Redis must not be touched")
        ),
    )

    task = {
        "id": "task-123",
        "output_format": "bogus_format",
        "add_subtitles": "yes",
        # string shape is the defensive legacy form; dict is the asyncpg shape.
        "cleanup_settings_json": json.dumps({"pause_threshold_ms": 99999}),
    }

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(task)

    # invalid format falls back to default vertical
    assert settings["output_format"] == "vertical"
    # non-boolean add_subtitles falls back to default True
    assert settings["add_subtitles"] is True
    # pause threshold is clamped into the valid range
    assert settings["pause_threshold_ms"] == 3000


def test_merge_task_source_metadata_backfills_required_fields():
    merged = _merge_task_source_metadata(
        {},
        source_url="upload://demo.mp4",
        source_type="video_url",
        output_format="vertical",
        add_subtitles=True,
        cleanup_settings={
            "cut_long_pauses": True,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        },
    )

    assert merged == {
        "url": "upload://demo.mp4",
        "source_type": "video_url",
        "output_format": "vertical",
        "add_subtitles": True,
        "cut_long_pauses": True,
        "pause_threshold_ms": 900,
        "remove_filler_words": False,
        "filtered_words": [],
    }


def test_merge_task_source_metadata_preserves_existing_render_settings():
    merged = _merge_task_source_metadata(
        {
            "url": "upload://demo.mp4",
            "source_type": "video_url",
            "output_format": "original",
            "add_subtitles": False,
        },
        cleanup_settings={
            "cut_long_pauses": True,
            "pause_threshold_ms": 1200,
            "remove_filler_words": True,
            "filtered_words": ["basically"],
        },
    )

    assert merged["output_format"] == "original"
    assert merged["add_subtitles"] is False
    assert merged["cut_long_pauses"] is True
    assert merged["pause_threshold_ms"] == 1200
    assert merged["remove_filler_words"] is True
    assert merged["filtered_words"] == ["basically"]


def test_merge_task_source_metadata_accepts_smart_vertical_modes():
    merged = _merge_task_source_metadata(
        {},
        source_url="upload://demo.mp4",
        source_type="video_url",
        output_format="vertical_split",
        add_subtitles=True,
    )

    assert merged["output_format"] == "vertical_split"


@pytest.mark.asyncio
async def test_load_task_source_settings_accepts_speaker_pan_mode(monkeypatch):
    payload = '{"output_format": "vertical_pan", "add_subtitles": true}'
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: _RedisClient(payload),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(_legacy_task_dict())

    assert settings["output_format"] == "vertical_pan"


@pytest.mark.asyncio
async def test_load_task_source_settings_falls_back_when_key_missing(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: _RedisClient(None),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(_legacy_task_dict("task-missing"))

    assert settings == _default_settings()


@pytest.mark.asyncio
async def test_load_task_source_settings_falls_back_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: _RedisClient("this is { not json"),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(_legacy_task_dict("task-corrupt"))

    assert settings == _default_settings()


@pytest.mark.asyncio
async def test_load_task_source_settings_sanitizes_invalid_values(monkeypatch):
    payload = json.dumps(
        {
            "output_format": "bogus_format",
            "add_subtitles": "yes",
            "pause_threshold_ms": 99999,
            "cut_long_pauses": True,
            "remove_filler_words": True,
            "filtered_words": " Um , UM,hello ",
        }
    )
    monkeypatch.setattr(
        "src.services.task_metadata_service.get_redis_client",
        lambda **_kwargs: _RedisClient(payload),
    )

    service = TaskService(db=None)
    settings = await service._load_task_source_settings(_legacy_task_dict())

    # invalid format falls back to default vertical
    assert settings["output_format"] == "vertical"
    # non-boolean add_subtitles falls back to default True
    assert settings["add_subtitles"] is True
    # pause threshold is clamped into the valid range
    assert settings["pause_threshold_ms"] == 3000
    assert settings["cut_long_pauses"] is True
    assert settings["remove_filler_words"] is True
    assert settings["filtered_words"] == ["um", "hello"]
