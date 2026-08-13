"""Offline dispatch tests for VideoService.generate_transcript provider switch.

Contract clauses under falsification:
- transcript_provider == "local_asr" -> get_video_transcript_local is invoked
  (AssemblyAI path untouched).
- any other provider (default "assemblyai") -> get_video_transcript is invoked
  (local path untouched).

run_in_thread is stubbed so no real transcription code (local HTTP or
AssemblyAI) ever runs; nothing here touches the network, ffmpeg, or config
state outside the monkeypatched seam.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import video_service as video_service_module
from src.services.video_service import VideoService


async def _stub_dispatch_harness(monkeypatch, provider: str) -> dict:
    """Stub run_in_thread + get_config; returns the recorded call dict."""
    calls: dict = {}

    async def fake_run_in_thread(func, *args, **kwargs):
        calls["func"] = func
        calls["args"] = args
        calls["kwargs"] = kwargs
        return "transcript-result"

    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)
    config = SimpleNamespace(
        transcript_provider=provider,
        fast_mode_transcript_model="nano",
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    return calls


@pytest.mark.asyncio
async def test_generate_transcript_local_asr_provider_invokes_local_function(
    monkeypatch,
) -> None:
    calls = await _stub_dispatch_harness(monkeypatch, "local_asr")
    video_path = Path("/videos/sample.mp4")

    result = await VideoService.generate_transcript(
        video_path, processing_mode="fast"
    )

    assert result == "transcript-result"
    assert calls["func"] is video_service_module.get_video_transcript_local
    assert calls["args"] == (video_path, "nano")


@pytest.mark.parametrize("provider", ["assemblyai", "custom_provider"])
@pytest.mark.asyncio
async def test_generate_transcript_non_local_provider_invokes_assemblyai_path(
    monkeypatch, provider
) -> None:
    calls = await _stub_dispatch_harness(monkeypatch, provider)
    video_path = Path("/videos/sample.mp4")

    result = await VideoService.generate_transcript(
        video_path, processing_mode="fast"
    )

    assert result == "transcript-result"
    assert calls["func"] is video_service_module.get_video_transcript
    assert calls["args"] == (video_path, "nano")


# --- process_video_complete sidecar guard (contract clause under falsification):
# When cached_transcript is supplied, the word-timing sidecar
# (<video_path>.transcript_cache.json) is the only source for word-synced
# captions. The guard must regenerate the transcript whenever cached transcript
# text exists WITHOUT its sidecar, and must keep the fast path when the sidecar
# is present. Only cache_transcript_data (called inside generate_transcript)
# writes the sidecar, so skipping generation starves caption rendering.
# run_in_thread is never reached: cached_analysis_json with non-empty
# most_relevant_segments routes the pipeline past AI analysis and clip
# rendering, stopping the test exactly at the guard point.

_CACHED_ANALYSIS_JSON = (
    '{"most_relevant_segments":[{"start_time":"00:00","end_time":"00:05",'
    '"text":"hello"}],"summary":null,"key_topics":[]}'
)

_SIDECAR_JSON = (
    '{"version":2,"words":[{"text":"hello","start":0,"end":500,'
    '"confidence":1.0}],"utterances":[],"text":"hello"}'
)


def _patch_pipeline_env(monkeypatch, video_path: Path, calls: list) -> None:
    """Stub the process_video_complete seams; records generate_transcript calls."""
    config = SimpleNamespace(
        transcript_provider="local_asr",
        fast_mode_transcript_model="nano",
        max_video_duration=5400,
        fast_mode_max_clips=4,
        clip_duration=30,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService, "resolve_local_video_path", lambda url: video_path
    )
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda path: None)

    async def fake_generate_transcript(video_path, processing_mode=None):
        calls.append((video_path, processing_mode))
        return "regenerated transcript"

    monkeypatch.setattr(VideoService, "generate_transcript", fake_generate_transcript)


@pytest.mark.asyncio
async def test_cached_transcript_without_sidecar_triggers_regeneration(
    monkeypatch, tmp_path
) -> None:
    # Precondition: sidecar is ABSENT next to the video file.
    video_file = tmp_path / "fake.mp4"
    video_file.write_bytes(b"not a real mp4")
    assert not (tmp_path / "fake.transcript_cache.json").exists()

    calls: list = []
    _patch_pipeline_env(monkeypatch, video_file, calls)

    await VideoService.process_video_complete(
        url="upload://fake.mp4",
        source_type="video_url",
        cached_transcript="cached transcript text",
        cached_analysis_json=_CACHED_ANALYSIS_JSON,
    )

    # Guard claim: cached transcript WITHOUT its word-timing sidecar must still
    # run generate_transcript so the sidecar gets written by cache_transcript_data.
    assert calls == [(video_file, "fast")]


@pytest.mark.asyncio
async def test_cached_transcript_with_sidecar_skips_regeneration(
    monkeypatch, tmp_path
) -> None:
    # Precondition: valid sidecar EXISTS next to the video file.
    video_file = tmp_path / "fake.mp4"
    video_file.write_bytes(b"not a real mp4")
    (tmp_path / "fake.transcript_cache.json").write_text(_SIDECAR_JSON)

    calls: list = []
    _patch_pipeline_env(monkeypatch, video_file, calls)

    result = await VideoService.process_video_complete(
        url="upload://fake.mp4",
        source_type="video_url",
        cached_transcript="cached transcript text",
        cached_analysis_json=_CACHED_ANALYSIS_JSON,
    )

    # Control claim: valid sidecar present -> fast path preserved, no regeneration.
    assert calls == []
    assert result["transcript"] == "cached transcript text"
