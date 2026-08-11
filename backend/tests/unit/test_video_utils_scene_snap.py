"""
Falsification tests for snap_keep_ranges_end_to_scene_cut and the scene-cut
integration inside create_optimized_clip (src/video_utils.py).

Pure snap contract:
  (a) a scene cut inside (end, end + max_snap_seconds] snaps the last range's
      end forward to that cut;
  (b) a scene cut exactly at the end (c == end) never qualifies (only c > end),
      so the end does not move;
  (c) no cut inside the window -> ranges unchanged;
  (d) a cut before the end is ignored (the end never moves backward);
  (e) empty/None keep_ranges or empty scene_cuts -> unchanged;
  (f) with multiple cuts inside the window the nearest one after the end wins.

Integration contract (create_optimized_clip, fast path):
  - when extend_keep_ranges_to_sentence_boundary moves the end, the scene-cut
    detector runs on [last_start, extended_end + 1.5] and the rendered -t
    reflects the snapped end;
  - when extension does not move the end, no detection/snap runs and -t keeps
    the sentence-boundary end.
"""

from __future__ import annotations

import pytest

from src import video_utils
from src.video_utils import (
    create_optimized_clip,
    snap_keep_ranges_end_to_scene_cut,
)


class TestSnapKeepRangesEndToSceneCut:
    def test_cut_inside_window_snaps_last_range_end(self):
        result = snap_keep_ranges_end_to_scene_cut(
            [(10.0, 12.0), (13.0, 15.0)], [15.3]
        )
        assert result == [(10.0, 12.0), (13.0, 15.3)]

    def test_cut_exactly_at_end_is_not_snapped(self):
        result = snap_keep_ranges_end_to_scene_cut([(10.0, 15.0)], [15.0])
        assert result == [(10.0, 15.0)]

    def test_cut_beyond_snap_window_is_not_snapped(self):
        result = snap_keep_ranges_end_to_scene_cut(
            [(10.0, 15.0)], [16.2], max_snap_seconds=1.0
        )
        assert result == [(10.0, 15.0)]

    def test_no_cut_within_window_is_unchanged(self):
        result = snap_keep_ranges_end_to_scene_cut(
            [(10.0, 15.0)], [16.5]
        )
        assert result == [(10.0, 15.0)]

    def test_cut_before_end_is_ignored_never_moves_backward(self):
        # 12.0 sits before the 15.0 end and must not pull it back; 15.3 is the
        # qualifying cut.
        result = snap_keep_ranges_end_to_scene_cut(
            [(10.0, 15.0)], [12.0, 15.3]
        )
        assert result == [(10.0, 15.3)]

    def test_empty_keep_ranges_are_unchanged(self):
        assert snap_keep_ranges_end_to_scene_cut([], [15.3]) == []
        assert snap_keep_ranges_end_to_scene_cut(None, [15.3]) is None

    def test_empty_scene_cuts_are_unchanged(self):
        ranges = [(10.0, 15.0)]
        assert snap_keep_ranges_end_to_scene_cut(ranges, []) == ranges
        assert snap_keep_ranges_end_to_scene_cut(ranges, None) == ranges

    def test_nearest_cut_after_end_wins(self):
        result = snap_keep_ranges_end_to_scene_cut(
            [(10.0, 15.0)], [15.9, 15.2, 15.7], max_snap_seconds=1.0
        )
        assert result == [(10.0, 15.2)]

    def test_max_snap_seconds_clamps_the_window(self):
        # 16.5 is inside the default window but snap is capped at 1.0s here.
        assert snap_keep_ranges_end_to_scene_cut(
            [(10.0, 15.0)], [16.5], max_snap_seconds=1.0
        ) == [(10.0, 15.0)]
        assert snap_keep_ranges_end_to_scene_cut(
            [(10.0, 15.0)], [16.5], max_snap_seconds=2.0
        ) == [(10.0, 16.5)]


class TestCreateOptimizedClipSceneSnapIntegration:
    def _run(self, monkeypatch, tmp_path, fake_extend, fake_detect, keep_ranges):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")
        output = tmp_path / "out.mp4"

        calls = {"detect": [], "run": []}

        def wrapped_extend(_video_path, ranges, max_extension_seconds=8.0):
            return fake_extend(_video_path, ranges)

        def wrapped_detect(_video_path, start_seconds, end_seconds, scene_threshold=0.4):
            calls["detect"].append((start_seconds, end_seconds, scene_threshold))
            return fake_detect(_video_path, start_seconds, end_seconds, scene_threshold)

        class FakeResult:
            returncode = 0
            stderr = ""

        class FakeSubprocess:
            @staticmethod
            def run(command, *args, **kwargs):
                calls["run"].append(command)
                return FakeResult()

        monkeypatch.setattr(
            video_utils, "extend_keep_ranges_to_sentence_boundary", wrapped_extend
        )
        monkeypatch.setattr(video_utils, "detect_scene_cuts_in_window", wrapped_detect)
        monkeypatch.setattr(video_utils, "subprocess", FakeSubprocess)

        ok = create_optimized_clip(
            video,
            10.0,
            12.0,
            output,
            add_subtitles=False,
            output_format="original",
            keep_ranges=keep_ranges,
        )
        return ok, calls

    @staticmethod
    def _stream_copy_t(cmd):
        # Fast path command: ffmpeg -y -ss <start> -i <video> -t <dur> ...
        assert "-ss" in cmd and "-t" in cmd
        return float(cmd[cmd.index("-t") + 1])

    def test_snap_applied_when_extension_moves_end(self, monkeypatch, tmp_path):
        # Extension moves 12.0 -> 14.0; a cut at 14.6 sits inside the 1.0s snap
        # window, so the rendered -t must reflect 14.6 - 10.0 = 4.6.
        def fake_extend(_video_path, ranges):
            assert ranges == [(10.0, 12.0)]
            return [(10.0, 14.0)]

        def fake_detect(_video_path, start_seconds, end_seconds, scene_threshold=0.4):
            assert start_seconds == 10.0
            assert end_seconds == pytest.approx(15.5)
            assert scene_threshold == pytest.approx(0.4)
            return [14.6]

        ok, calls = self._run(
            monkeypatch, tmp_path, fake_extend, fake_detect, [(10.0, 12.0)]
        )
        assert ok is True
        assert len(calls["detect"]) == 1
        assert len(calls["run"]) == 1
        assert self._stream_copy_t(calls["run"][0]) == pytest.approx(4.6)

    def test_no_snap_when_extension_does_not_move_end(self, monkeypatch, tmp_path):
        # Extension leaves the end at 12.0; the detector must not run and the
        # rendered -t stays 12.0 - 10.0 = 2.0.
        def fake_extend(_video_path, ranges):
            return [(10.0, 12.0)]

        def fake_detect(*_args, **_kwargs):
            raise AssertionError("detect_scene_cuts_in_window must not run")

        ok, calls = self._run(
            monkeypatch, tmp_path, fake_extend, fake_detect, [(10.0, 12.0)]
        )
        assert ok is True
        assert calls["detect"] == []
        assert len(calls["run"]) == 1
        assert self._stream_copy_t(calls["run"][0]) == pytest.approx(2.0)

    def test_detect_failure_degrades_silently(self, monkeypatch, tmp_path):
        # A detector exception must not fail the clip; the sentence-boundary
        # end (14.0) is kept.
        def fake_extend(_video_path, ranges):
            return [(10.0, 14.0)]

        def fake_detect(*_args, **_kwargs):
            raise RuntimeError("ffmpeg exploded")

        ok, calls = self._run(
            monkeypatch, tmp_path, fake_extend, fake_detect, [(10.0, 12.0)]
        )
        assert ok is True
        assert len(calls["detect"]) == 1
        assert self._stream_copy_t(calls["run"][0]) == pytest.approx(4.0)
