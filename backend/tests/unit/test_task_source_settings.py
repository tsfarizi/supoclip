import json

import pytest

from src.services.task_service import TaskService


def _task_row(**overrides) -> dict:
    """A task row with every Schema v2 render-settings column NULL (a legacy
    row that was never backfilled).

    _load_task_render_settings takes the task dict returned by
    get_task_by_id and resolves settings from the DB columns only; a NULL
    column resolves to the documented default. The legacy
    task_source:{task_id} Redis cache no longer exists (P4).
    """
    row = {
        "id": "task-123",
        "output_format": None,
        "add_subtitles": None,
        "cleanup_settings_json": None,
        "hook_persist": None,
        "watermark": None,
        "watermark_persist": None,
        "sound_effects_count": None,
    }
    row.update(overrides)
    return row


def _default_settings() -> dict:
    return {
        "output_format": "vertical",
        "add_subtitles": True,
        "cut_long_pauses": False,
        "pause_threshold_ms": 900,
        "remove_filler_words": False,
        "filtered_words": [],
        "hook_persist": False,
        "watermark": None,
        "watermark_persist": False,
        "sound_effects_count": 0,
    }


@pytest.mark.asyncio
async def test_load_task_render_settings_null_columns_resolve_to_defaults():
    # A fully-NULL legacy row resolves to the documented defaults; there is no
    # Redis read path anymore, so nothing to fall back to.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row())

    assert settings == _default_settings()


@pytest.mark.asyncio
async def test_load_task_render_settings_uses_db_columns():
    # DB-first contract: populated Schema v2 columns are the single authority
    # and are resolved without any Redis involvement.
    task = _task_row(
        output_format="original",
        add_subtitles=False,
        # asyncpg 0.31 decodes the jsonb column to a dict natively.
        cleanup_settings_json={
            "cut_long_pauses": True,
            "pause_threshold_ms": 1400,
            "remove_filler_words": True,
            "filtered_words": ["basically", "like"],
        },
    )

    service = TaskService(db=None)
    settings = await service._load_task_render_settings(task)

    assert settings == {
        "output_format": "original",
        "add_subtitles": False,
        "cut_long_pauses": True,
        "pause_threshold_ms": 1400,
        "remove_filler_words": True,
        "filtered_words": ["basically", "like"],
        "hook_persist": False,
        "watermark": None,
        "watermark_persist": False,
        "sound_effects_count": 0,
    }


@pytest.mark.asyncio
async def test_load_task_render_settings_sanitizes_invalid_db_columns():
    task = _task_row(
        output_format="bogus_format",
        add_subtitles="yes",
        # string shape is the defensive legacy form; dict is the asyncpg shape.
        cleanup_settings_json=json.dumps({"pause_threshold_ms": 99999}),
    )

    service = TaskService(db=None)
    settings = await service._load_task_render_settings(task)

    # invalid format falls back to default vertical
    assert settings["output_format"] == "vertical"
    # non-boolean add_subtitles falls back to default True
    assert settings["add_subtitles"] is True
    # pause threshold is clamped into the valid range
    assert settings["pause_threshold_ms"] == 3000


@pytest.mark.asyncio
async def test_load_task_render_settings_sanitizes_invalid_values():
    task = _task_row(
        output_format="bogus_format",
        add_subtitles="yes",
        cleanup_settings_json={
            "pause_threshold_ms": 99999,
            "cut_long_pauses": True,
            "remove_filler_words": True,
            "filtered_words": " Um , UM,hello ",
        },
    )

    service = TaskService(db=None)
    settings = await service._load_task_render_settings(task)

    # invalid format falls back to default vertical
    assert settings["output_format"] == "vertical"
    # non-boolean add_subtitles falls back to default True
    assert settings["add_subtitles"] is True
    # pause threshold is clamped into the valid range
    assert settings["pause_threshold_ms"] == 3000
    assert settings["cut_long_pauses"] is True
    assert settings["remove_filler_words"] is True
    assert settings["filtered_words"] == ["um", "hello"]


@pytest.mark.asyncio
async def test_load_task_render_settings_accepts_smart_vertical_modes():
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(
        _task_row(output_format="vertical_pan")
    )

    assert settings["output_format"] == "vertical_pan"


# ---------------------------------------------------------------------------
# hook_persist (Schema v3): BOOLEAN NOT NULL DEFAULT false; the DB column is
# the single authority.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_task_render_settings_defaults_hook_persist_to_false():
    # A legacy row (hook_persist NULL) must resolve to the column default false.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row())

    assert settings["hook_persist"] is False


@pytest.mark.asyncio
async def test_load_task_render_settings_db_hook_persist_true_resolves_true():
    # The DB column True is authoritative and resolves to True.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row(hook_persist=True))

    assert settings["hook_persist"] is True


@pytest.mark.asyncio
async def test_load_task_render_settings_non_bool_hook_persist_falls_back_to_false():
    # A non-boolean hook_persist from the DB column is not trusted: the
    # resolved value must fall back to the default false.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row(hook_persist="yes"))

    assert settings["hook_persist"] is False


# ---------------------------------------------------------------------------
# watermark / watermark_persist plumbing: watermark TEXT (nullable),
# watermark_persist BOOLEAN NOT NULL DEFAULT false; the DB column is the
# single authority.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_task_render_settings_defaults_watermark_none_and_persist_false():
    # A legacy row with NULL watermark columns must resolve to watermark None
    # and watermark_persist False.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row())

    assert settings["watermark"] is None
    assert settings["watermark_persist"] is False


@pytest.mark.asyncio
async def test_load_task_render_settings_db_watermark_wins():
    # Populated DB watermark columns resolve directly.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(
        _task_row(watermark="db-handle", watermark_persist=True)
    )

    assert settings["watermark"] == "db-handle"
    assert settings["watermark_persist"] is True


@pytest.mark.asyncio
async def test_load_task_render_settings_non_bool_watermark_persist_falls_back_to_false():
    # A non-boolean watermark_persist from the DB column is not trusted: the
    # resolved value must fall back to the default false.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(
        _task_row(watermark="some-handle", watermark_persist="yes")
    )

    assert settings["watermark_persist"] is False


# ---------------------------------------------------------------------------
# sound_effects_count (Schema v2): INTEGER NOT NULL DEFAULT 0 with a 0..5
# CHECK constraint; the DB column is the single authority.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_task_render_settings_defaults_sound_effects_count_to_zero():
    # A NULL column resolves to the column default 0.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row())

    assert settings["sound_effects_count"] == 0


@pytest.mark.asyncio
async def test_load_task_render_settings_clamps_out_of_range_sound_effects_count():
    # Values outside the 0..5 domain are clamped like the API/worker boundary.
    service = TaskService(db=None)
    settings = await service._load_task_render_settings(_task_row(sound_effects_count=9))

    assert settings["sound_effects_count"] == 5