"""Falsification tests for the auto-aspect + smart-fit vertical reframing
(src/video_utils.py).

Covers Opsi 1 (resolve_auto_output_format: a content-dominated clip resolves to
landscape 16:9; a face-dominated clip stays vertical 9:16; portrait/narrow
sources always stay vertical) and Opsi 2 (build_vertical_compositor_filter with
fit_fill renders horizontal content scaled-to-fill instead of letterboxed).
"""

from pathlib import Path

from src import video_utils
from src.video_utils import (
    AUTO_LANDSCAPE_OUTPUT,
    build_vertical_compositor_filter,
    build_vertical_filter_plan,
    resolve_auto_output_format,
    _fit_crop_x_fraction,
)

WIDTH = 1920
HEIGHT = 1080


def _track(entries):
    return [(float(t), cx, float(a)) for t, cx, a in entries]


def _face_only_plan_analysis():
    """A 30s clip with a face everywhere -> 100% face, no fit scenes."""
    track = _track([(t, 960.0, 0.03) for t in range(0, 30, 2)])
    scene_cuts = []
    return track, [], scene_cuts


def _content_dominated_analysis():
    """A 30s clip with a face only in the first 6s, then face-free frames
    (slides/screen content) for the remaining 24s -> 80% content time."""
    track = _track([(t, 960.0, 0.03) for t in range(0, 6, 2)])
    track += _track([(t, None, 0.0) for t in range(6, 30, 2)])
    scene_cuts = []
    return track, [], scene_cuts


class TestResolveAutoOutputFormat:
    def test_content_dominated_resolves_to_landscape(self, monkeypatch):
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (WIDTH, HEIGHT))
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 30.0)
        result = resolve_auto_output_format(Path("x.mp4"), _content_dominated_analysis())
        assert result == AUTO_LANDSCAPE_OUTPUT

    def test_face_dominated_resolves_to_vertical(self, monkeypatch):
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (WIDTH, HEIGHT))
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 30.0)
        result = resolve_auto_output_format(Path("x.mp4"), _face_only_plan_analysis())
        assert result == "vertical"

    def test_portrait_source_stays_vertical(self, monkeypatch):
        # 9:16 or narrower source can never be shown as 16:9.
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (1080, 1920))
        result = resolve_auto_output_format(Path("x.mp4"), _content_dominated_analysis())
        assert result == "vertical"

    def test_empty_track_stays_vertical(self, monkeypatch):
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (WIDTH, HEIGHT))
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 30.0)
        result = resolve_auto_output_format(Path("x.mp4"), ([], [], []))
        assert result == "vertical"

    def test_unreadable_video_stays_vertical(self, monkeypatch):
        def boom(_path):
            raise RuntimeError("no video")

        monkeypatch.setattr(video_utils, "ffprobe_video_size", boom)
        assert resolve_auto_output_format(Path("x.mp4")) == "vertical"


class TestFitCropXFraction:
    def test_no_faces_in_content_shots_centers(self):
        track = _track([(t, None, 0.0) for t in (0, 1, 2, 3, 4)])
        assert _fit_crop_x_fraction(track, [(0.0, 5.0)], 1920) == 0.5

    def test_median_of_faces_inside_content_shots(self):
        track = _track([(t, x, 0.02) for t, x in ((0.0, 960.0), (1.0, 1440.0), (2.0, 480.0))])
        # Median x fraction = 960/1920 = 0.5.
        assert _fit_crop_x_fraction(track, [(0.0, 3.0)], 1920) == 0.5

    def test_faces_outside_content_shots_ignored(self):
        track = _track([(t, 1920.0, 0.02) for t in (0.0, 1.0, 2.0)])
        assert _fit_crop_x_fraction(track, [(5.0, 10.0)], 1920) == 0.5

    def test_extreme_median_is_clamped(self):
        track = _track([(t, 100.0, 0.02) for t in (0.0, 1.0, 2.0)])
        assert _fit_crop_x_fraction(track, [(0.0, 3.0)], 1920) == 0.2


class TestCompositorFitFill:
    def test_fit_fill_uses_increase_and_crop(self):
        chain = build_vertical_compositor_filter(
            "crop=600:1080:0:0,scale=1080:1920", [(0.0, 2.0)], [(2.0, 5.0)]
        )
        assert "force_original_aspect_ratio=increase" in chain
        assert "crop=1080:1920:x='trunc((iw-1080)*0.500/2)*2'" in chain

    def test_fit_fill_false_keeps_letterbox(self):
        chain = build_vertical_compositor_filter(
            "crop=600:1080:0:0,scale=1080:1920",
            [(0.0, 2.0)],
            [(2.0, 5.0)],
            fit_fill=False,
        )
        # Isolate the fit-source segment (starts with the label [ftsrc] as a
        # filter input); background uses increase for its own blur fill.
        fit_part = next(
            seg for seg in chain.split(";") if seg.startswith("[ftsrc]")
        )
        assert "force_original_aspect_ratio=decrease" in fit_part
        assert "force_original_aspect_ratio=increase" not in fit_part


class TestBuildVerticalFilterPlanSmartFit:
    def test_content_clip_builds_fill_compositor(self, monkeypatch, tmp_path):
        clip = tmp_path / "clip.mp4"
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 30.0)
        monkeypatch.setattr(
            video_utils,
            "analyze_vertical_clip_multi",
            lambda p: _content_dominated_analysis(),
        )
        monkeypatch.setattr(video_utils, "detect_speaker_reframe_plan", lambda *a, **k: None)

        video_filter, mode = build_vertical_filter_plan(clip, WIDTH, HEIGHT)

        assert mode == "complex"
        assert "force_original_aspect_ratio=increase" in video_filter
        assert "[vout]" in video_filter


class TestRenderLandscapeBranch:
    def test_landscape_renders_16x9_full_frame(self, monkeypatch, tmp_path):
        input_path = tmp_path / "in.mp4"
        output_path = tmp_path / "out.mp4"
        calls = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, timeout=900):
            calls.append(command)
            return Result()

        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (1920, 1080))
        monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda p: True)
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 10.0)
        monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run)

        ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
            input_path,
            output_path,
            AUTO_LANDSCAPE_OUTPUT,
        )

        assert ok is True
        assert (out_w, out_h) == (1920, 1080)
        render_command = calls[-1]
        vf = render_command[render_command.index("-vf") + 1]
        assert "scale=1920:1080" in vf
        assert "fade=t=out" in vf  # fade still applies in landscape

    def test_auto_resolves_landscape_when_content_dominated(self, monkeypatch, tmp_path):
        input_path = tmp_path / "in.mp4"
        output_path = tmp_path / "out.mp4"
        calls = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, timeout=900):
            calls.append(command)
            return Result()

        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (1920, 1080))
        monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda p: True)
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 30.0)
        monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run)
        monkeypatch.setattr(
            video_utils,
            "analyze_vertical_clip_multi",
            lambda p: _content_dominated_analysis(),
        )

        ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
            input_path,
            output_path,
            "auto",
        )

        assert ok is True
        assert (out_w, out_h) == (1920, 1080)

    def test_auto_resolves_vertical_when_face_dominated(self, monkeypatch, tmp_path):
        input_path = tmp_path / "in.mp4"
        output_path = tmp_path / "out.mp4"
        calls = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, timeout=900):
            calls.append(command)
            return Result()

        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (1920, 1080))
        monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda p: True)
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 30.0)
        monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run)
        monkeypatch.setattr(
            video_utils,
            "analyze_vertical_clip_multi",
            lambda p: _face_only_plan_analysis(),
        )

        ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
            input_path,
            output_path,
            "auto",
        )

        assert ok is True
        assert (out_w, out_h) == (1080, 1920)
