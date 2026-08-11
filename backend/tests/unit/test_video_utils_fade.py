"""
Falsification tests for the end-of-clip fade-out in src/video_utils.py.

Two contracts are covered:

  1. build_fade_out_args — a pure helper producing the ffmpeg `fade` video
     fragment and the `afade` audio argument. Normal durations get both with
     the right start times; non-positive and near-empty durations return None
     so the render falls back to no fade.

  2. render_reframed_clip_ffmpeg — the default vertical vf branch appends the
     video fade last (after subtitles) and adds a second `-af afade` when the
     clip has audio; the original (copy/subs-only) branch never fades; and a
     clip without audio gets the video fade but no `-af` at all.
"""

from pathlib import Path

from src import video_utils
from src.video_utils import VIDEO_FADE_FRAMES, build_fade_out_args


class TestBuildFadeOutArgs:
    def test_normal_duration_builds_video_and_audio_fragments(self):
        video_fragment, audio_af_arg = build_fade_out_args(10.0)
        video_duration = VIDEO_FADE_FRAMES / video_utils.OUTPUT_FPS

        assert video_fragment == (
            f"fade=t=out:st={10.0 - video_duration:.3f}:d={video_duration:.3f}"
        )
        assert audio_af_arg == "afade=t=out:st=9.700:d=0.300"

    def test_non_positive_duration_returns_none(self):
        assert build_fade_out_args(0.0) is None
        assert build_fade_out_args(-5.0) is None

    def test_too_short_duration_returns_none(self):
        assert build_fade_out_args(0.2) is None
        assert build_fade_out_args(0.3) is None
        assert build_fade_out_args(0.34) is None
        assert build_fade_out_args(0.36) is not None

    def test_custom_fade_seconds_is_honoured(self):
        video_fragment, audio_af_arg = build_fade_out_args(5.0, fade_seconds=1.0)
        assert audio_af_arg == "afade=t=out:st=4.000:d=1.000"
        assert video_fragment.startswith("fade=t=out:st=")


class TestRenderReframedClipFadeOut:
    """Integration: the render command carries the fade only where contracted."""

    VF_FILTER = "crop=606:1080:100:0,scale=1080:1920:flags=lanczos,setsar=1"

    def _patch(self, monkeypatch, *, has_audio=True, captured=None):
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (1920, 1080))
        monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda p: has_audio)
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 10.0)
        monkeypatch.setattr(
            video_utils,
            "build_vertical_filter_plan",
            lambda p, w, h: (self.VF_FILTER, "vf"),
        )

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, timeout=900):
            captured.append(command)
            return Result()

        monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run)

    def test_default_vf_branch_appends_fade_last_and_audio_fade(
        self, monkeypatch, tmp_path
    ):
        captured = []
        self._patch(monkeypatch, captured=captured)

        ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            "vertical",
            subtitle_ass_path=tmp_path / "subs.ass",
        )

        assert ok is True
        assert (out_w, out_h) == (1080, 1920)
        command = captured[0]
        vf_value = command[command.index("-vf") + 1]
        assert ",fade=t=out" in vf_value
        assert vf_value.endswith("fade=t=out:st=9.933:d=0.067")
        assert "-af" in command
        assert "afade=t=out:st=9.700:d=0.300" in command

    def test_original_branch_has_no_fade(self, monkeypatch, tmp_path):
        captured = []
        self._patch(monkeypatch, captured=captured)

        ok, _, _ = video_utils.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            "original",
            subtitle_ass_path=tmp_path / "subs.ass",
        )

        assert ok is True
        command = captured[0]
        assert "fade" not in " ".join(command)
        assert not any(arg.startswith("afade=") for arg in command)

    def test_no_audio_means_video_fade_but_no_af(self, monkeypatch, tmp_path):
        captured = []
        self._patch(monkeypatch, has_audio=False, captured=captured)

        ok, _, _ = video_utils.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            "vertical",
        )

        assert ok is True
        command = captured[0]
        vf_value = command[command.index("-vf") + 1]
        assert vf_value.endswith("fade=t=out:st=9.933:d=0.067")
        assert "-an" in command
        assert "-af" not in command
