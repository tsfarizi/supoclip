import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.workers.tasks import WorkerSettings, render_composition_task


def test_worker_settings_registers_render_composition_task():
    func_names = [f.__name__ for f in WorkerSettings.functions]
    assert "render_composition_task" in func_names
    assert "process_video_task" in func_names


@pytest.mark.asyncio
async def test_render_composition_task_execution():
    ctx = {
        "redis": AsyncMock(),
    }
    task_id = "t-100"
    clip_id = "c-100"
    composition_dict = {
        "schema_version": 1,
        "source_asset_ref": {
            "source_id": "src-1",
            "source_url": "https://youtube.com/watch?v=abc",
        },
        "output": {"format": "vertical", "preset": "tiktok"},
        "segments": [
            {
                "id": "seg-1",
                "source_start": 0.0,
                "source_end": 10.0,
                "reframe": {"mode": "track"},
                "speed": {"rate": 1.0},
                "audio": {"take_source": True, "gain_db": 0.0},
                "caption": {"template": "default"},
            }
        ],
        "broll_inserts": [],
        "sfx": [],
        "transitions": [],
    }

    mock_db = AsyncMock()
    mock_db_cm = MagicMock()
    mock_db_cm.__aenter__.return_value = mock_db
    mock_db_cm.__aexit__.return_value = None

    with patch("src.infra.db.AsyncSessionLocal", return_value=mock_db_cm), \
         patch("src.shared.config.runtime_settings.load_runtime_settings_cache", new_callable=AsyncMock), \
         patch("src.shared.config.runtime_settings.get_render_concurrency", return_value=2), \
         patch("src.domain.media.resolver.SourceAssetResolver.resolve", new_callable=AsyncMock) as mock_resolve, \
         patch("src.domain.media.render_engine.RenderEngine.render") as mock_render, \
         patch("src.video_utils.ffprobe_duration", return_value=10.0), \
         patch("src.infra.db.repositories.clip_repository.ClipRepository.get_clip_by_id", new_callable=AsyncMock) as mock_get_clip, \
         patch("src.infra.db.repositories.clip_repository.ClipRepository.update_clip_render_result", new_callable=AsyncMock) as mock_update:

        from pathlib import Path
        mock_resolve.return_value = Path("/tmp/source.mp4")
        mock_render.return_value = Path("/tmp/out/clip_123.mp4")
        mock_get_clip.return_value = {
            "id": clip_id,
            "task_id": task_id,
            "composition_version": 1,
            "clip_order": 1,
        }
        mock_update.return_value = True

        result = await render_composition_task(
            ctx=ctx,
            task_id=task_id,
            clip_id=clip_id,
            composition_dict=composition_dict,
            user_id="u-1",
        )

        assert result["task_id"] == task_id
        assert result["clip_id"] == clip_id
        assert result["duration"] == 10.0
        assert result["composition_version"] == 2
        mock_update.assert_called_once()
