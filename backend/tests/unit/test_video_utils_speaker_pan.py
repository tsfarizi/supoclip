"""
Falsification tests for the automatic speaker-pan gate in src/video_utils.py.

Two contracts are covered:

  1. should_use_speaker_pan — a pure gate deciding whether a detected pan plan
     is worth using in the default vertical mode. It must reject non-pan plans,
     None plans, plans for clips with more than two scene cuts, and plans whose
     face regions are too small, while accepting a pan plan whose two regions
     cover enough of the source frame. Plans that carry no region geometry
     (the pan plans detect_speaker_reframe_plan actually returns) pass the
     area gate because it is not evaluable there — the plan already implies
     two detected, separated faces.

  2. build_vertical_filter_plan — the default-vertical integration. When the
     clip has few scene cuts it probes the speaker pan plan and returns the
     pan crop chain when the gate passes; otherwise the legacy scene-aware
     layout is unchanged. All ffmpeg/face work is monkeypatched so the tests
     run without any video or external process.
"""

from pathlib import Path

import pytest

from src import video_utils
from src.video_utils import should_use_speaker_pan

WIDTH = 1920
HEIGHT = 1080
FRAME_AREA = float(WIDTH * HEIGHT)


def _pan_plan(*, regions=None, width=WIDTH, height=HEIGHT):
    plan = {
        "mode": "pan",
        "width": width,
        "height": height,
        "crop_w": 606,
        "crop_h": height,
        "x_expression": "trunc((100+(t*10))/2)*2",
    }
    if regions is not None:
        plan["regions"] = regions
    return plan


def _region(roi_w, roi_h):
    return {"roi_w": roi_w, "roi_h": roi_h}


BIG_REGIONS = {"left": _region(420, 270), "right": _region(420, 270)}  # ~0.055 frac
SMALL_REGIONS = {"left": _region(120, 80), "right": _region(120, 80)}  # ~0.005 frac
THRESHOLD_REGIONS = {"left": _region(288, 216), "right": _region(288, 216)}  # 0.03 frac


class TestShouldUseSpeakerPan:
    def test_pan_plan_with_large_regions_and_two_cuts_returns_true(self):
        plan = _pan_plan(regions=BIG_REGIONS)
        assert should_use_speaker_pan(plan, 2) is True
        assert should_use_speaker_pan(plan, [1.0, 2.0]) is True

    def test_regions_at_exact_threshold_are_accepted(self):
        plan = _pan_plan(regions=THRESHOLD_REGIONS)
        assert should_use_speaker_pan(plan, 2) is True

    def test_more_than_two_scene_cuts_returns_false(self):
        plan = _pan_plan(regions=BIG_REGIONS)
        assert should_use_speaker_pan(plan, 3) is False
        assert should_use_speaker_pan(plan, [1.0, 2.0, 4.0]) is False

    def test_none_plan_returns_false(self):
        assert should_use_speaker_pan(None, 2) is False

    def test_non_pan_mode_returns_false(self):
        plan = _pan_plan(regions=BIG_REGIONS)
        plan["mode"] = "split"
        assert should_use_speaker_pan(plan, 2) is False

    def test_small_regions_below_threshold_return_false(self):
        plan = _pan_plan(regions=SMALL_REGIONS)
        assert should_use_speaker_pan(plan, 2) is False

    def test_custom_area_threshold_is_honoured(self):
        plan = _pan_plan(regions=BIG_REGIONS)
        assert should_use_speaker_pan(plan, 2, area_threshold=0.1) is False
        assert should_use_speaker_pan(plan, 2, area_threshold=0.01) is True

    def test_incomplete_regions_return_false(self):
        plan = _pan_plan(regions={"left": _region(420, 270)})
        assert should_use_speaker_pan(plan, 2) is False

    def test_plan_without_regions_passes_area_gate(self):
        # Real pan plans from detect_speaker_reframe_plan carry no region
        # geometry; the gate is not evaluable and must not reject them.
        assert should_use_speaker_pan(_pan_plan(), 2) is True


class TestBuildVerticalFilterPlanSpeakerPan:
    """Integration: default vertical mode uses the pan chain when qualified."""

    def test_returns_pan_chain_when_plan_qualifies(self, monkeypatch, tmp_path):
        clip = tmp_path / "clip.mp4"
        pan_plan = _pan_plan(regions=BIG_REGIONS)

        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 10.0)
        monkeypatch.setattr(
            video_utils, "analyze_vertical_clip", lambda p: ([], [1.0, 2.0])
        )
        monkeypatch.setattr(
            video_utils, "detect_speaker_reframe_plan",
            lambda p, fmt: pan_plan,
        )

        video_filter, mode = video_utils.build_vertical_filter_plan(
            clip, WIDTH, HEIGHT
        )

        assert mode == "vf"
        assert "crop=606:1080:x='" in video_filter
        assert "trunc((100+(t*10))/2)*2" in video_filter
        assert ":y=0," in video_filter
        assert "scale=1080:1920:flags=lanczos,setsar=1" in video_filter

    def test_legacy_path_unchanged_when_plan_is_none(self, monkeypatch, tmp_path):
        clip = tmp_path / "clip.mp4"

        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 10.0)
        monkeypatch.setattr(
            video_utils, "analyze_vertical_clip", lambda p: ([], [1.0, 2.0])
        )
        monkeypatch.setattr(video_utils, "detect_speaker_reframe_plan", lambda p, fmt: None)
        monkeypatch.setattr(
            video_utils, "detect_optimal_crop_region", lambda p, s, e: (100, 0, 608, 1080)
        )

        video_filter, mode = video_utils.build_vertical_filter_plan(
            clip, WIDTH, HEIGHT
        )

        assert mode == "vf"
        assert "crop=606:1080:100:0," in video_filter
        assert "crop=606:1080:x='" not in video_filter

    def test_pan_is_not_probed_when_clip_has_many_scene_cuts(self, monkeypatch, tmp_path):
        clip = tmp_path / "clip.mp4"
        probe_calls = {"n": 0}

        def fail_if_called(p, fmt):
            probe_calls["n"] += 1
            raise AssertionError("pan probe must be skipped for 3-cut clips")

        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 10.0)
        monkeypatch.setattr(
            video_utils, "analyze_vertical_clip", lambda p: ([], [1.0, 2.0, 4.0])
        )
        monkeypatch.setattr(video_utils, "detect_speaker_reframe_plan", fail_if_called)
        monkeypatch.setattr(
            video_utils, "detect_optimal_crop_region", lambda p, s, e: (100, 0, 608, 1080)
        )

        video_filter, mode = video_utils.build_vertical_filter_plan(
            clip, WIDTH, HEIGHT
        )

        assert probe_calls["n"] == 0
        assert mode == "vf"
        assert "crop=606:1080:100:0," in video_filter
