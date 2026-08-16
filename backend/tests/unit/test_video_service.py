import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import video_service as video_service_module
from src.services.video_service import VideoService
from src.clip_source_map import load_clip_source_manifest


class _EmptyAnalysis:
    summary = "No strong standalone segment found"
    key_topics = []
    most_relevant_segments = []


@pytest.mark.asyncio
async def test_process_video_complete_uses_fallback_when_ai_selects_no_segments(
    monkeypatch, tmp_path
):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")

    config = SimpleNamespace(
        max_video_duration=5400,
        clip_duration=30,
        fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService,
        "resolve_local_video_path",
        staticmethod(lambda _url: video_path),
    )
    monkeypatch.setattr(
        VideoService,
        "_get_file_duration",
        staticmethod(lambda _path: 42.0),
    )

    async def fake_generate_transcript(_video_path, processing_mode="balanced"):
        return "[00:00 - 00:01] hello"

    async def fake_analyze_transcript(
        _transcript, clip_signals=None, include_broll=False, visual_signals=None
    ):
        return _EmptyAnalysis()

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
        url="upload://source.mp4",
        source_type="video_url",
        processing_mode="fast",
    )

    assert result["segments_to_render"] == [
        {
            "start_time": "00:00",
            "end_time": "00:30",
            "text": "[00:00 - 00:01] hello",
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so SupoClip generated the first available portion of the video."
            ),
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
            "hook_title": None,
        }
    ]
    analysis = json.loads(result["analysis_json"])
    assert analysis["most_relevant_segments"] == result["segments_to_render"]


def test_fallback_segment_caps_to_video_duration():
    segment = VideoService._build_fallback_segment(
        video_duration=12.0,
        transcript="short transcript",
        target_duration=30,
    )

    assert segment["start_time"] == "00:00"
    assert segment["end_time"] == "00:12"
    assert segment["hook_type"] == "fallback"


@pytest.mark.asyncio
async def test_create_single_clip_composes_hook_before_main_and_persists_roles(
    monkeypatch, tmp_path
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    config = SimpleNamespace(temp_dir=str(tmp_path))
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        video_service_module,
        "build_clip_keep_ranges",
        lambda *_args, **_kwargs: [(20.0, 25.0)],
    )
    monkeypatch.setattr(
        video_service_module,
        "extend_keep_ranges_to_sentence_boundary",
        lambda _path, ranges, **_kwargs: ranges,
    )
    captured = {}

    def fake_compose(video_path, hook_range, main_ranges, output_path, **kwargs):
        captured["video_path"] = video_path
        captured["hook_range"] = hook_range
        captured["main_ranges"] = main_ranges
        Path(output_path).write_bytes(b"rendered")
        return Path(output_path)

    async def fake_run_in_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(video_service_module, "compose_hook_and_main", fake_compose)
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)
    monkeypatch.setattr(
        video_service_module,
        "apply_broll_suggestions_to_clip",
        lambda *_args, **_kwargs: None,
    )

    clip = await VideoService.create_single_clip(
        source,
        {
            "start_time": "00:20",
            "end_time": "00:25",
            "text": "main",
            "hook_selection": {
                "hook_start_time": "00:15",
                "hook_end_time": "00:20",
                "transcript_evidence": "setup",
                "reasoning": "curiosity",
                "hook_score": 10,
            },
        },
        0,
        tmp_path,
    )

    assert clip is not None
    assert captured["hook_range"] == (15.0, 20.0)
    assert captured["main_ranges"] == [(20.0, 25.0)]
    manifest = load_clip_source_manifest(Path(clip["path"]))
    assert manifest["hook_range"] == (15.0, 20.0)
    assert manifest["main_ranges"] == [(20.0, 25.0)]
