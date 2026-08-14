from pathlib import Path

import pytest

from src.services.video_service import VideoService
from src.clip_source_map import load_clip_source_ranges, save_clip_source_ranges
from src.video_utils import (
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    create_optimized_clip,
    create_clips_from_segments,
    get_words_for_keep_ranges,
)


def test_build_clip_keep_ranges_removes_pauses_and_filler_words(monkeypatch):
    transcript_data = {
        "words": [
            {"text": "Hey", "start": 0, "end": 300},
            {"text": "um", "start": 300, "end": 500},
            {"text": "let's", "start": 1700, "end": 2100},
            {"text": "go", "start": 2100, "end": 2400},
        ]
    }
    monkeypatch.setattr(
        "src.video_utils.load_cached_transcript_data",
        lambda _video_path: transcript_data,
    )

    keep_ranges = build_clip_keep_ranges(
        Path("/tmp/demo.mp4"),
        0.0,
        2.4,
        {
            "cut_long_pauses": True,
            "pause_threshold_ms": 900,
            "remove_filler_words": True,
            "filtered_words": [],
        },
    )

    assert keep_ranges == [(0.0, 0.3), (1.7, 2.4)]


def test_build_clip_keep_ranges_removes_boundary_silence(monkeypatch):
    transcript_data = {
        "words": [
            {"text": "Hello", "start": 1000, "end": 1300},
            {"text": "there", "start": 1300, "end": 1600},
        ]
    }
    monkeypatch.setattr(
        "src.video_utils.load_cached_transcript_data",
        lambda _video_path: transcript_data,
    )

    keep_ranges = build_clip_keep_ranges(
        Path("/tmp/demo.mp4"),
        0.0,
        2.8,
        {
            "cut_long_pauses": True,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        },
    )

    assert keep_ranges == [(1.0, 1.6)]


def test_get_words_for_keep_ranges_retimes_words_into_output_timeline():
    transcript_data = {
        "words": [
            {"text": "first", "start": 0, "end": 300},
            {"text": "second", "start": 1000, "end": 1300},
            {"text": "third", "start": 1300, "end": 1600},
        ]
    }

    words = get_words_for_keep_ranges(
        transcript_data,
        [(0.0, 0.3), (1.0, 1.6)],
    )

    assert [word["text"] for word in words] == ["first", "second", "third"]
    assert [word["confidence"] for word in words] == [1.0, 1.0, 1.0]
    assert [word["start"] for word in words] == pytest.approx([0.0, 0.3, 0.6])
    assert [word["end"] for word in words] == pytest.approx([0.3, 0.6, 0.9])


def test_build_keep_ranges_from_source_ranges_recomputes_each_range(monkeypatch):
    calls: list[tuple[float, float]] = []

    def fake_build_clip_keep_ranges(_video_path, clip_start, clip_end, _settings):
        calls.append((clip_start, clip_end))
        return [(clip_start + 0.1, clip_end - 0.1)]

    monkeypatch.setattr(
        "src.video_utils.build_clip_keep_ranges",
        fake_build_clip_keep_ranges,
    )

    keep_ranges = build_keep_ranges_from_source_ranges(
        Path("/tmp/demo.mp4"),
        [(10.0, 11.0), (13.0, 15.0)],
        {"cut_long_pauses": True},
    )

    assert calls == [(10.0, 11.0), (13.0, 15.0)]
    assert keep_ranges == [(10.1, 10.9), (13.1, 14.9)]


@pytest.mark.asyncio
async def test_create_single_clip_keeps_timing_fields_consistent(monkeypatch, tmp_path):
    async def fake_run_in_thread(fn, *_args, **_kwargs):
        # Simulate a successful clip render (returns True) but a no-op B-roll
        # pass (returns None, matching apply_broll_suggestions_to_clip when
        # there are no suggestions); a truthy broll result would make
        # create_single_clip unlink/rename a clip file that the mocked render
        # never created.
        if fn.__name__ == "create_optimized_clip":
            return True
        return None

    monkeypatch.setattr("src.services.video_service.run_in_thread", fake_run_in_thread)
    monkeypatch.setattr(
        "src.services.video_service.build_clip_keep_ranges",
        lambda *_args, **_kwargs: [(10.0, 11.0), (12.0, 14.0)],
    )

    clip = await VideoService.create_single_clip(
        video_path=Path("/tmp/demo.mp4"),
        segment={"start_time": "00:10", "end_time": "00:20", "text": "hello"},
        clip_index=0,
        output_dir=tmp_path,
        add_subtitles=False,
        cleanup_settings={"cut_long_pauses": True},
    )

    assert clip is not None
    assert clip["duration"] == pytest.approx(3.0)
    assert clip["start_time"] == "00:10"
    assert clip["end_time"] == "00:20"


def test_create_clips_from_segments_keeps_timing_fields_consistent(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "src.video_utils.build_clip_keep_ranges",
        lambda *_args, **_kwargs: [(10.0, 11.0), (12.0, 14.0)],
    )
    monkeypatch.setattr("src.video_utils.create_optimized_clip", lambda *_args, **_kwargs: True)

    clips = create_clips_from_segments(
        video_path=Path("/tmp/demo.mp4"),
        segments=[
            {
                "start_time": "00:10",
                "end_time": "00:20",
                "text": "hello",
                "relevance_score": 0.8,
                "reasoning": "test",
            }
        ],
        output_dir=tmp_path,
        add_subtitles=False,
        cleanup_settings={"cut_long_pauses": True},
    )

    assert len(clips) == 1
    assert clips[0]["duration"] == pytest.approx(3.0)
    assert clips[0]["start_time"] == "00:10"
    assert clips[0]["end_time"] == "00:20"


def test_create_clips_from_segments_uses_source_ranges_when_recomputing_cleanup(
    monkeypatch, tmp_path
):
    captured: dict[str, object] = {}

    def fake_build_keep_ranges_from_source_ranges(
        _video_path, source_ranges, cleanup_settings
    ):
        captured["source_ranges"] = source_ranges
        captured["cleanup_settings"] = cleanup_settings
        return [(10.1, 10.9), (13.1, 14.9)]

    monkeypatch.setattr(
        "src.video_utils.build_keep_ranges_from_source_ranges",
        fake_build_keep_ranges_from_source_ranges,
    )
    monkeypatch.setattr(
        "src.video_utils.create_optimized_clip", lambda *_args, **_kwargs: True
    )

    clips = create_clips_from_segments(
        video_path=Path("/tmp/demo.mp4"),
        segments=[
            {
                "start_time": "00:10",
                "end_time": "00:15",
                "source_ranges": [(10.0, 11.0), (13.0, 15.0)],
                "text": "hello",
                "relevance_score": 0.8,
                "reasoning": "test",
            }
        ],
        output_dir=tmp_path,
        add_subtitles=False,
        cleanup_settings={"cut_long_pauses": True},
    )

    assert len(clips) == 1
    assert captured["source_ranges"] == [(10.0, 11.0), (13.0, 15.0)]
    assert captured["cleanup_settings"] == {"cut_long_pauses": True}
    assert clips[0]["duration"] == pytest.approx(2.6)


@pytest.mark.asyncio
async def test_apply_single_transition_copies_clip_source_map(monkeypatch, tmp_path):
    current_clip_path = tmp_path / "clip.mp4"
    current_clip_path.write_bytes(b"clip")
    save_clip_source_ranges(current_clip_path, [(10.0, 11.0), (12.0, 14.0)])

    clip_info = await VideoService.apply_single_transition(
        prev_clip_path=tmp_path / "prev.mp4",
        current_clip_info={
            "filename": "clip.mp4",
            "path": str(current_clip_path),
        },
        clip_index=1,
        output_dir=tmp_path,
    )

    transitioned_path = Path(clip_info["path"])
    assert load_clip_source_ranges(transitioned_path) == [(10.0, 11.0), (12.0, 14.0)]


def test_original_never_uses_stream_copy(monkeypatch, tmp_path):
    """Original format ALWAYS runs the full render pipeline — no `-c copy`.

    The stream-copy fast path (add_subtitles=False + original + single keep
    range) is REMOVED from the contract. Every original clip must go through
    render_source_ranges_ffmpeg -> render_reframed_clip_ffmpeg and land on the
    1080x1920 WHITE canvas (pad=1080:1920 ... color=white).
    """
    commands: list[list[str]] = []

    class _CompletedProcess:
        returncode = 0
        stderr = ""

    def fake_subprocess_run(command, **_kwargs):
        # The removed fast path shells out via subprocess.run directly
        # (bypassing run_ffmpeg_command). Keep capturing it so any resurrected
        # fast path is caught by the `-c copy` assertion below.
        commands.append(command)
        return _CompletedProcess()

    def fake_run_ffmpeg_command(command, **_kwargs):
        commands.append(command)
        # The final render command's output (final.mp4) must exist for the
        # subsequent shutil.move to succeed.
        Path(command[-1]).write_bytes(b"video")
        return _CompletedProcess()

    def fake_render_source_ranges_ffmpeg(_video_path, _keep_ranges, output_path):
        Path(output_path).write_bytes(b"video")
        return True

    def fake_build_assemblyai_ass_subtitles(*args, **_kwargs):
        # Defensive seam: if the new pipeline builds an ASS sidecar even for a
        # captionless render, hand it a minimal valid file.
        ass_path = args[5]
        ass_path.write_text(
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr(
        "src.video_utils.render_source_ranges_ffmpeg",
        fake_render_source_ranges_ffmpeg,
    )
    monkeypatch.setattr("src.video_utils.ffprobe_video_size", lambda _path: (640, 360))
    monkeypatch.setattr("src.video_utils.ffprobe_has_audio", lambda _path: False)
    monkeypatch.setattr("src.video_utils.ffprobe_duration", lambda _path: 10.0)
    monkeypatch.setattr(
        "src.video_utils.build_assemblyai_ass_subtitles",
        fake_build_assemblyai_ass_subtitles,
    )
    monkeypatch.setattr(
        "src.video_utils.run_ffmpeg_command", fake_run_ffmpeg_command
    )
    monkeypatch.setattr("src.video_utils.shutil.move", lambda _src, _dst: None)

    success = create_optimized_clip(
        video_path=Path("/tmp/demo.mp4"),
        start_time=10.0,
        end_time=20.0,
        output_path=tmp_path / "clip.mp4",
        add_subtitles=False,
        output_format="original",
        keep_ranges=[(10.5, 20.0)],
    )

    def _is_stream_copy(command):
        return any(
            token == "-c"
            and index + 1 < len(command)
            and command[index + 1] == "copy"
            for index, token in enumerate(command)
        )

    assert success is True
    assert commands, "original render must shell out at least once"
    assert not any(_is_stream_copy(command) for command in commands), (
        "original format must NEVER use stream copy: "
        f"found `-c copy` in {commands!r}"
    )
    assert any(
        "pad=1080:1920" in arg for command in commands for arg in command
    ), "original render must pad onto a 1080x1920 canvas"
    assert any(
        "color=white" in arg for command in commands for arg in command
    ), "original render must use a WHITE pad colour"
