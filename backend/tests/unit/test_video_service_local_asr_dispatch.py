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
