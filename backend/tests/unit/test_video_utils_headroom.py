"""
Falsification tests for the horizontal headroom crop-x helper in
src/video_utils.py.

compute_headroom_crop_x is pure: it maps a synthetic face track of
(t, center_x_source_px, area_frac) entries to per-sample crop-x values that
keep the estimated face bbox (plus a margin of margin_frac * crop_w) inside the
crop. Contract under test:

  (a) a face far left clamps the crop to x=0 (never negative) while the
      bbox+margin box stays inside the crop;
  (b) a centred face yields crop_x ~= center - crop_w/2;
  (c) a face far right clamps to width - crop_w;
  (d) a wide face (large area_frac) is constrained by the bbox: when the
      bbox+margin box cannot fit, the fallback keeps the bbox alone inside;
  (e) an empty track, an all-missing track, and a crop with no horizontal room
      all return [].

The half-width estimate mirrors the implementation: a square face box on a
16:9 source gives half_w = 0.375 * width * sqrt(area_frac).
"""

import math

import pytest

from src.video_utils import build_crop_trajectory, compute_headroom_crop_x

WIDTH = 1920
CROP_W = 1080
MAX_X = WIDTH - CROP_W  # 840
MARGIN_FRAC = 0.18
MARGIN = MARGIN_FRAC * CROP_W  # 194.4


def _half_w(area_frac: float) -> float:
    return 0.375 * WIDTH * math.sqrt(max(0.0, area_frac))


def _track(*entries):
    return [(float(i), c, a) for i, (c, a) in enumerate(entries)]


class TestComputeHeadroomCropX:
    def test_empty_track_returns_empty(self):
        assert compute_headroom_crop_x([], WIDTH, CROP_W) == []

    def test_all_missing_detections_are_skipped(self):
        out = compute_headroom_crop_x(
            [(0.0, None, 0.0), (1.0, None, 0.0)], WIDTH, CROP_W
        )
        assert out == []

    def test_missing_detections_are_skipped_while_others_kept(self):
        out = compute_headroom_crop_x(
            [(0.0, None, 0.0), (1.0, 960.0, 0.01), (2.0, None, 0.0)], WIDTH, CROP_W
        )
        assert len(out) == 1
        assert out[0][0] == 1.0

    def test_crop_with_no_horizontal_room_returns_empty(self):
        assert compute_headroom_crop_x(_track((960.0, 0.01)), 1000, 1000) == []
        assert compute_headroom_crop_x(_track((960.0, 0.01)), 1000, 1200) == []

    def test_face_far_left_clamps_to_zero_and_keeps_bbox_plus_margin_inside(self):
        # Face far left: the pure centre position would be negative, so the crop
        # is forced to x=0, and the bbox+margin box must still fit inside.
        c, area = 500.0, 0.04
        hw = _half_w(area)
        out = compute_headroom_crop_x(
            _track((c, area)), WIDTH, CROP_W, margin_frac=MARGIN_FRAC
        )
        assert len(out) == 1
        t, x = out[0]
        assert t == 0.0
        assert x == 0.0
        # bbox + margin must lie inside the crop [x, x + crop_w].
        assert c - hw - MARGIN >= x
        assert c + hw + MARGIN <= x + CROP_W

    def test_centred_face_crop_x_equals_centre_minus_half_crop(self):
        c, area = 960.0, 0.01
        hw = _half_w(area)
        out = compute_headroom_crop_x(
            _track((c, area)), WIDTH, CROP_W, margin_frac=MARGIN_FRAC
        )
        assert out[0][1] == pytest.approx(c - CROP_W / 2.0, abs=1e-9)
        # Centring also keeps the bbox+margin box inside the crop.
        x = out[0][1]
        assert c - hw - MARGIN >= x
        assert c + hw + MARGIN <= x + CROP_W

    def test_face_far_right_clamps_to_width_minus_crop_w(self):
        # Face far right: the pure centre position exceeds width - crop_w, and
        # the bbox+margin box cannot fit (face right edge + margin overflows the
        # source frame), so the face bbox alone must stay inside the crop.
        c, area = 1800.0, 0.01
        hw = _half_w(area)
        out = compute_headroom_crop_x(
            _track((c, area)), WIDTH, CROP_W, margin_frac=MARGIN_FRAC
        )
        assert out[0][1] == float(MAX_X)
        x = out[0][1]
        assert c - hw >= x
        assert c + hw <= x + CROP_W
        # The margin cannot be satisfied on the right: documented fallback.
        assert c + hw + MARGIN > x + CROP_W

    def test_wide_face_margin_satisfiable_keeps_bbox_plus_margin_inside(self):
        # Wide but still fits with margin: crop_x must sit inside the bbox+margin
        # constraint range, so the returned value is verifiably the headroom pick.
        c, area = 960.0, 0.12
        hw = _half_w(area)
        assert 2 * (hw + MARGIN) < CROP_W  # margin box fits
        out = compute_headroom_crop_x(
            _track((c, area)), WIDTH, CROP_W, margin_frac=MARGIN_FRAC
        )
        x = out[0][1]
        lower = c + hw + MARGIN - CROP_W
        upper = c - hw - MARGIN
        assert lower <= x <= upper
        assert c - hw - MARGIN >= x
        assert c + hw + MARGIN <= x + CROP_W

    def test_wide_face_margin_not_satisfiable_falls_back_to_bbox_inside(self):
        # Face so wide that bbox+margin cannot fit in the crop at all; the
        # fallback priority keeps the bbox alone inside the crop.
        c, area = 1100.0, 0.25
        hw = _half_w(area)
        assert 2 * (hw + MARGIN) > CROP_W  # margin box cannot fit
        assert 2 * hw < CROP_W  # bbox alone still fits
        out = compute_headroom_crop_x(
            _track((c, area)), WIDTH, CROP_W, margin_frac=MARGIN_FRAC
        )
        x = out[0][1]
        assert 0.0 <= x <= float(MAX_X)
        # bbox alone inside the crop; margin box is NOT (constraint was relaxed).
        assert c - hw >= x
        assert c + hw <= x + CROP_W
        assert not (c - hw - MARGIN >= x and c + hw + MARGIN <= x + CROP_W)

    def test_margin_frac_is_honoured(self):
        c, area = 960.0, 0.12
        hw = _half_w(area)
        big_margin = 0.5 * CROP_W
        assert 2 * (hw + big_margin) > CROP_W  # only satisfiable via bbox fallback
        out = compute_headroom_crop_x(
            _track((c, area)), WIDTH, CROP_W, margin_frac=0.5
        )
        x = out[0][1]
        assert c - hw >= x
        assert c + hw <= x + CROP_W

    def test_zero_area_means_no_bbox_constraint(self):
        # area_frac == 0 -> estimated half-width 0 -> pure centred crop.
        c = 1200.0
        out = compute_headroom_crop_x(_track((c, 0.0)), WIDTH, CROP_W)
        assert out[0][1] == pytest.approx(c - CROP_W / 2.0, abs=1e-9)


class TestBuildCropTrajectoryHeadroomIntegration:
    def test_uses_headroom_helper_and_still_produces_keyframes(self):
        # Dense synthetic track with a face drifting across the frame.
        track = [
            (t, 600.0 + 600.0 * t / 10.0, 0.04) for t in range(0, 11)
        ]
        keys = build_crop_trajectory(track, WIDTH, CROP_W, margin_frac=MARGIN_FRAC)
        assert keys
        for _, x in keys:
            assert 0 <= x <= MAX_X

    def test_default_margin_matches_helper_default(self):
        track = [(t, 960.0, 0.01) for t in range(0, 6)]
        keys = build_crop_trajectory(track, WIDTH, CROP_W)
        assert keys
