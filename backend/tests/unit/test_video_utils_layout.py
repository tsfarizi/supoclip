"""
Falsification tests for build_layout_plan (src/video_utils.py) — the per-scene
vertical layout classifier.

Covered contracts:
  1. Timeline is partitioned per scene: boundaries are 0.0, every valid scene
     cut (0.05 < c < duration - 0.05), and duration; no valid cut -> a single
     [0, duration] scene.
  2. One decision per scene by face-presence MAJORITY over that scene's frames
     (rate >= FACE_PRESENCE_RATE -> "face", else "fit"). This is the core
     regression: face presence rising and falling INSIDE one scene never flips
     the layout mid-scene.
  3. Runs are emitted exactly on scene boundaries and adjacent runs with the
     same kind coalesce.
  4. Edge cases: empty track -> face; duration <= 0 -> face; exactly-threshold
     rate (0.25) -> face (>=); filtered boundary cuts; scene without frames
     inherits the previous scene's decision (opening empty scene -> face).

These helpers are pure; importing video_utils pulls heavy deps (cv2, aai,
httpx) which are already available to the existing video_utils test files.
"""

from __future__ import annotations

import pytest

from src.video_utils import build_layout_plan

FACE_AREA = 0.05  # above FACE_PRESENCE_MIN_AREA (0.002)


def face(t: float) -> tuple[float, float, float]:
    """(time, confidence, area) frame with a detected face."""
    return (t, 0.95, FACE_AREA)


def noface(t: float) -> tuple[float, float, float]:
    """(time, confidence, area) frame with no detected face."""
    return (t, None, 0.0)


class TestLayoutRegressionNoMidSceneFlip:
    def test_face_present_absent_present_in_one_scene_stays_face(self):
        # Scene 0-3 has a face that drops out mid-scene (present/absent/present).
        # Majority (2/3) must win for the WHOLE scene; scene 3-5 is no-face, so
        # exactly two runs with the boundary at the scene cut — no extra run
        # appears inside 0-3.
        track = [
            face(0.5),
            noface(1.5),
            face(2.5),
            noface(3.5),
            noface(4.5),
        ]
        plan = build_layout_plan(track, [3.0], 5.0)
        assert plan == [
            {"start": 0.0, "end": 3.0, "kind": "face"},
            {"start": 3.0, "end": 5.0, "kind": "fit"},
        ]

    def test_absent_majority_in_scene_stays_fit(self):
        # Present/absent/present with a longer absent tail: 0-3 is face-majority
        # (2/3), 3-5 is fit — still exactly two runs.
        track = [
            face(0.5),
            noface(1.5),
            face(2.5),
            noface(3.2),
            noface(3.8),
            noface(4.5),
        ]
        plan = build_layout_plan(track, [3.0], 5.0)
        assert plan == [
            {"start": 0.0, "end": 3.0, "kind": "face"},
            {"start": 3.0, "end": 5.0, "kind": "fit"},
        ]


class TestLayoutTwoScenes:
    def test_face_majority_then_no_face_yields_two_runs_at_cut(self):
        track = [
            face(0.5),
            face(1.5),
            noface(2.5),
            noface(3.5),
            noface(4.5),
        ]
        plan = build_layout_plan(track, [3.0], 5.0)
        assert len(plan) == 2
        assert plan[0] == {"start": 0.0, "end": 3.0, "kind": "face"}
        assert plan[1]["start"] == pytest.approx(3.0)
        assert plan[1]["end"] == pytest.approx(5.0)
        assert plan[1]["kind"] == "fit"


class TestLayoutNoSceneCuts:
    def test_mixed_presence_without_cuts_is_one_face_run(self):
        # No scene cut -> a single scene; a mid-clip detection drop must NOT
        # create a layout flip.
        track = [
            face(0.5),
            noface(1.5),
            face(2.5),
            noface(3.5),
        ]
        plan = build_layout_plan(track, None, 5.0)
        assert plan == [{"start": 0.0, "end": 5.0, "kind": "face"}]

    def test_mixed_presence_without_cuts_is_one_fit_run(self):
        track = [
            face(0.5),
            noface(1.5),
            noface(2.5),
            noface(3.5),
            noface(4.5),
        ]
        plan = build_layout_plan(track, [], 5.0)
        assert plan == [{"start": 0.0, "end": 5.0, "kind": "fit"}]


class TestLayoutUniformPresence:
    def test_all_face_is_one_face_run(self):
        track = [face(0.5), face(1.5), face(2.5)]
        plan = build_layout_plan(track, [3.0], 5.0)
        assert plan == [{"start": 0.0, "end": 5.0, "kind": "face"}]

    def test_all_no_face_is_one_fit_run(self):
        track = [noface(0.5), noface(1.5), noface(2.5)]
        plan = build_layout_plan(track, [3.0], 5.0)
        assert plan == [{"start": 0.0, "end": 5.0, "kind": "fit"}]


class TestLayoutCoalesceNeighbours:
    def test_adjacent_scenes_with_same_kind_coalesce(self):
        track = [
            face(0.5),
            face(1.5),
            face(2.5),
            face(3.5),
            face(4.5),
        ]
        plan = build_layout_plan(track, [2.0, 3.0, 4.0], 5.0)
        assert plan == [{"start": 0.0, "end": 5.0, "kind": "face"}]


class TestLayoutEdgeCases:
    def test_invalid_boundaries_are_filtered(self):
        # 0.02 is <= 0.05 and 5.5 is >= duration - 0.05: both must be dropped.
        # Only 1.0 remains as a boundary -> exactly two runs.
        track = [
            face(0.5),
            noface(2.0),
            noface(3.0),
            noface(4.0),
        ]
        plan = build_layout_plan(track, [0.02, 1.0, 5.5], 5.0)
        assert plan == [
            {"start": 0.0, "end": 1.0, "kind": "face"},
            {"start": 1.0, "end": 5.0, "kind": "fit"},
        ]

    def test_empty_track_is_face(self):
        assert build_layout_plan([], [3.0], 5.0) == [
            {"start": 0.0, "end": 5.0, "kind": "face"}
        ]

    def test_duration_zero_is_face(self):
        assert build_layout_plan([face(0.5)], [3.0], 0.0) == [
            {"start": 0.0, "end": 0.0, "kind": "face"}
        ]

    def test_negative_duration_is_face(self):
        plan = build_layout_plan([face(0.5)], [3.0], -2.0)
        assert plan == [{"start": 0.0, "end": 0.0, "kind": "face"}]

    def test_exact_threshold_rate_counts_as_face(self):
        # 1 present / 4 frames = 0.25 == FACE_PRESENCE_RATE -> face (>=).
        track = [
            face(0.5),
            noface(1.5),
            noface(2.5),
            noface(3.5),
        ]
        plan = build_layout_plan(track, None, 4.0)
        assert plan == [{"start": 0.0, "end": 4.0, "kind": "face"}]


class TestLayoutEmptyScenes:
    def test_opening_scene_without_frames_defaults_to_face(self):
        # No frames in scene 0-1; scene 1-5 is all no-face. The opening scene
        # has no previous neighbour -> face, then the populated scene is fit.
        track = [
            noface(1.5),
            noface(2.5),
            noface(3.5),
            noface(4.5),
        ]
        plan = build_layout_plan(track, [1.0], 5.0)
        assert plan == [
            {"start": 0.0, "end": 1.0, "kind": "face"},
            {"start": 1.0, "end": 5.0, "kind": "fit"},
        ]

    def test_empty_scene_inherits_previous_scene_decision(self):
        # Scene 0-2 is all no-face (fit); scene 2-5 has NO frames. The empty
        # scene must inherit fit from its neighbour — not default to face — so
        # the two runs coalesce into a single fit run.
        track = [
            noface(0.5),
            noface(1.5),
        ]
        plan = build_layout_plan(track, [2.0], 5.0)
        assert plan == [{"start": 0.0, "end": 5.0, "kind": "fit"}]
