"""Falsification tests for temporal face tracking (src/face_tracking.py).

Contracts: detections of one person across frames form a single track; two
separate persons form two tracks without identity swaps; a gap longer than the
timeout finalises a track; too-short/too-sparse tracks are dropped as noise;
the dominant track is the one with the largest mean face area.
"""

from src.face_tracking import (
    TRACK_TIMEOUT_SECONDS,
    build_face_tracks,
    dominant_track,
)


def _det(x, y, w=40, h=40, conf=0.9):
    return (x, y, w, h, conf)


def _frames(entries):
    """Build (t, detections) samples from a list of (t, [det...])."""
    return [(float(t), dets) for t, dets in entries]


def _long_enough(offsets):
    """Return sample offsets that span >= MIN_TRACK_SECONDS (0.5s)."""
    return offsets if offsets[-1] - offsets[0] >= 0.5 else list(offsets) + [offsets[-1] + 0.5]


def test_single_person_forms_one_track():
    offsets = _long_enough([0.0, 0.2, 0.4])
    samples = _frames([(t, [_det(100, 100)]) for t in offsets])
    tracks = build_face_tracks(samples)
    assert len(tracks) == 1
    assert len(tracks[0].samples) == len(offsets)


def test_two_separate_persons_form_two_tracks():
    offsets = _long_enough([0.0, 0.2, 0.4])
    samples = _frames(
        [
            (t, [_det(100, 100), _det(600, 100)])
            for t in offsets
        ]
    )
    tracks = build_face_tracks(samples)
    assert len(tracks) == 2
    # No cross-identity merge: the left track never contains right-face boxes.
    left = min(tracks, key=lambda t: t.samples[0].x)
    assert all(s.x < 300 for s in left.samples)


def test_gap_longer_than_timeout_finalises_track():
    gap = TRACK_TIMEOUT_SECONDS + 0.5
    first_offsets = _long_enough([0.0, 0.2, 0.4])
    second_offsets = _long_enough([first_offsets[-1] + gap, first_offsets[-1] + gap + 0.2, first_offsets[-1] + gap + 0.4])
    samples = _frames(
        [(t, [_det(100, 100)]) for t in first_offsets]
        + [(t, [_det(600, 100)]) for t in second_offsets]
    )
    tracks = build_face_tracks(samples)
    assert len(tracks) == 2  # two separate appearances


def test_too_few_samples_dropped():
    first_offsets = _long_enough([0.0, 0.2, 0.4])
    gap = TRACK_TIMEOUT_SECONDS + 0.5
    samples = _frames(
        [(t, [_det(100, 100)]) for t in first_offsets]
        + [(first_offsets[-1] + gap, [_det(600, 100)]), (first_offsets[-1] + gap + 0.2, [_det(602, 101)])]
    )
    tracks = build_face_tracks(samples)
    # First track has 3 samples (kept); second has 2 (dropped below MIN).
    assert len(tracks) == 1
    assert tracks[0].samples[0].x < 300


def test_dominant_track_is_largest_mean_area():
    offsets = _long_enough([0.0, 0.2, 0.4])
    samples = _frames(
        [
            (t, [_det(100, 100, w=20, h=20), _det(600, 100, w=80, h=80)])
            for t in offsets
        ]
    )
    tracks = build_face_tracks(samples)
    assert len(tracks) == 2
    dom = dominant_track(tracks)
    assert dom is not None
    assert dom.samples[0].w == 80


def test_empty_input_returns_empty_list():
    assert build_face_tracks([]) == []
    assert dominant_track([]) is None
