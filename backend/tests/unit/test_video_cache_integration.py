"""Falsification tests for the video-cache integration.

U15 (youtube_utils): async_download_youtube_video serves a cache hit without
calling the download providers, and stores a fresh download into the cache on
the first call.
U16 (video_service): process_video_complete uses a validated cache entry to
skip both the YouTube metadata preflight and the download.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import video_cache, youtube_utils
from src.config import Config, set_config_override
from src.services import video_service as video_service_module
from src.services.video_service import VideoService

URL = "https://www.youtube.com/watch?v=abcdefghijk"
VIDEO_ID = "abcdefghijk"


@pytest.fixture
def cache_enabled(tmp_path, monkeypatch):
    config = Config()
    config.video_cache_enabled = True
    config.video_cache_dir = str(tmp_path / "cache")
    config.video_cache_ttl_hours = 24
    config.video_cache_max_gb = 0
    config.video_cache_skip_metadata = True
    set_config_override(config)
    monkeypatch.setattr(video_cache, "_probe_duration", lambda _p: 42.0)
    monkeypatch.setattr(video_cache, "_probe_size", lambda _p: (1280, 720))
    yield config
    set_config_override(None)


class TestDownloaderCacheIntegration:
    @pytest.mark.asyncio
    async def test_second_call_uses_cache_without_download(
        self, cache_enabled, tmp_path, monkeypatch
    ):
        async def no_lock(_video_id, timeout=3600.0):
            return True

        async def no_release(_video_id):
            return None

        download_calls = {"n": 0}

        def fake_download(_url, max_retries=3, task_id=None):
            download_calls["n"] += 1
            path = tmp_path / f"{VIDEO_ID}.mp4"
            path.write_bytes(b"x" * 4096)
            return path

        monkeypatch.setattr(youtube_utils, "acquire_video_download_lock", no_lock)
        monkeypatch.setattr(youtube_utils, "release_video_download_lock", no_release)
        monkeypatch.setattr(youtube_utils, "download_youtube_video", fake_download)

        first = await youtube_utils.async_download_youtube_video(URL, 3)
        assert first is not None
        assert download_calls["n"] == 1
        assert video_cache.lookup(VIDEO_ID) is not None

        second = await youtube_utils.async_download_youtube_video(URL, 3)
        assert second is not None
        assert download_calls["n"] == 1  # no re-download on cache hit
        assert second == video_cache._video_path(VIDEO_ID)

    @pytest.mark.asyncio
    async def test_cache_disabled_preserves_download_each_time(
        self, tmp_path, monkeypatch
    ):
        config = Config()
        config.video_cache_enabled = False
        config.temp_dir = str(tmp_path)
        set_config_override(config)
        try:
            async def no_lock(_video_id, timeout=3600.0):
                return True

            async def no_release(_video_id):
                return None

            download_calls = {"n": 0}

            def fake_download(_url, max_retries=3, task_id=None):
                download_calls["n"] += 1
                path = tmp_path / f"{VIDEO_ID}.mp4"
                path.write_bytes(b"x" * 4096)
                return path

            monkeypatch.setattr(youtube_utils, "acquire_video_download_lock", no_lock)
            monkeypatch.setattr(youtube_utils, "release_video_download_lock", no_release)
            monkeypatch.setattr(youtube_utils, "download_youtube_video", fake_download)
            # The legacy temp-dir reuse still runs when the cache is disabled;
            # disable it here so every call really reaches the downloader.
            monkeypatch.setattr(youtube_utils, "_find_existing_download", lambda _vid: None)

            await youtube_utils.async_download_youtube_video(URL, 3)
            await youtube_utils.async_download_youtube_video(URL, 3)
            assert download_calls["n"] == 2
        finally:
            set_config_override(None)


class TestPipelineCacheIntegration:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_metadata_and_download(
        self, cache_enabled, tmp_path, monkeypatch
    ):
        video_path = Path(cache_enabled.video_cache_dir) / f"{VIDEO_ID}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"x" * 4096)
        video_cache.store(
            video_path, VIDEO_ID, duration_seconds=30.0, width=1280, height=720
        )

        config = SimpleNamespace(
            max_video_duration=5400,
            clip_duration=30,
            fast_mode_max_clips=4,
            video_cache_enabled=True,
            video_cache_skip_metadata=True,
        )
        monkeypatch.setattr(video_service_module, "get_config", lambda: config)
        monkeypatch.setattr(
            VideoService,
            "_get_file_duration",
            staticmethod(lambda _path: 30.0),
        )
        monkeypatch.setattr(
            video_service_module,
            "async_get_youtube_video_info",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("metadata must be skipped")),
        )
        async def _download_must_not_run(*a, **k):
            raise AssertionError("download must be skipped")
        monkeypatch.setattr(
            VideoService,
            "download_video",
            staticmethod(_download_must_not_run),
        )

        async def fake_generate_transcript(_video_path, processing_mode="balanced"):
            return "[00:00 - 00:01] hello"

        async def fake_analyze_transcript(
            _transcript, clip_signals=None, include_broll=False,
            max_sfx_count=0, visual_signals=None
        ):
            return SimpleNamespace(
                summary="s",
                key_topics=[],
                most_relevant_segments=[],
                broll_opportunities=None,
            )

        async def fake_run_in_thread(_func, *_args, **_kwargs):
            return None

        monkeypatch.setattr(
            VideoService,
            "generate_transcript",
            staticmethod(fake_generate_transcript),
        )
        monkeypatch.setattr(
            VideoService,
            "analyze_transcript",
            staticmethod(fake_analyze_transcript),
        )
        monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)

        result = await VideoService.process_video_complete(
            url=URL,
            source_type="youtube",
            processing_mode="fast",
        )

        assert result["video_path"] == str(video_path)

    @pytest.mark.asyncio
    async def test_no_cache_hit_still_downloads(
        self, cache_enabled, tmp_path, monkeypatch
    ):
        # No cached entry for this video id: the normal metadata+download path
        # runs. Both calls are allowed here.
        config = SimpleNamespace(
            max_video_duration=5400,
            clip_duration=30,
            fast_mode_max_clips=4,
            video_cache_enabled=True,
            video_cache_skip_metadata=True,
        )
        monkeypatch.setattr(video_service_module, "get_config", lambda: config)
        monkeypatch.setattr(
            VideoService,
            "_get_file_duration",
            staticmethod(lambda _path: 42.0),
        )
        async def _fake_info(*a, **k):
            return {"duration": 60}
        monkeypatch.setattr(video_service_module, "async_get_youtube_video_info", _fake_info)
        video_path = tmp_path / "downloaded.mp4"
        video_path.write_bytes(b"x" * 4096)
        async def _fake_download(*a, **k):
            return video_path
        monkeypatch.setattr(
            VideoService,
            "download_video",
            staticmethod(_fake_download),
        )

        async def fake_generate_transcript(_video_path, processing_mode="balanced"):
            return "[00:00 - 00:01] hello"

        async def fake_analyze_transcript(
            _transcript, clip_signals=None, include_broll=False,
            max_sfx_count=0, visual_signals=None
        ):
            return SimpleNamespace(
                summary="s",
                key_topics=[],
                most_relevant_segments=[],
                broll_opportunities=None,
            )

        async def fake_run_in_thread(_func, *_args, **_kwargs):
            return None

        monkeypatch.setattr(
            VideoService,
            "generate_transcript",
            staticmethod(fake_generate_transcript),
        )
        monkeypatch.setattr(
            VideoService,
            "analyze_transcript",
            staticmethod(fake_analyze_transcript),
        )
        monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)

        result = await VideoService.process_video_complete(
            url=URL,
            source_type="youtube",
            processing_mode="fast",
        )

        assert result["video_path"] == str(video_path)
