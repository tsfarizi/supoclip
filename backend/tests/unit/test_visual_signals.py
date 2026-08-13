"""Falsification tests for deterministic visual signals (src/visual_signals.py).

The decode pass (analyze_vertical_clip) is monkeypatched so these tests run
without a real video. Contracts: an empty/unusable input yields ""; a valid
track yields the header plus one line per transcript span carrying face %,
cut count and a framing verdict; spans shorter than MIN_SPAN_SECONDS and spans
with no samples are skipped.
"""

from pathlib import Path

from src import visual_signals
from src.visual_signals import VISUAL_SIGNAL_HEADER, build_visual_signal_summary

VIDEO = Path("video.mp4")


def _track(entries):
    """Normalise a list of (t, cx_or_None, area) tuples."""
    return [(float(t), cx, float(a)) for t, cx, a in entries]


def test_empty_transcript_returns_empty(monkeypatch):
    assert build_visual_signal_summary(VIDEO, "") == ""
    assert build_visual_signal_summary(VIDEO, "not a timestamped line") == ""


def test_empty_track_returns_empty(monkeypatch):
    monkeypatch.setattr(visual_signals, "analyze_vertical_clip", lambda p: ([], []))
    transcript = "[00:00 - 00:30] a complete sentence."
    assert build_visual_signal_summary(VIDEO, transcript) == ""


def test_header_and_per_span_lines(monkeypatch):
    track = (
        _track([(t, 320.0, 0.02) for t in (10, 12, 14)])
        + _track([(t, None, 0.0) for t in (16, 18)])
        + _track([(t, 320.0, 0.02) for t in (20, 22)])
    )
    monkeypatch.setattr(visual_signals, "analyze_vertical_clip", lambda p: (track, []))
    transcript = (
        "[00:10 - 00:20] first span with mixed detections\n"
        "[00:40 - 00:50] second span entirely without samples"
    )
    summary = build_visual_signal_summary(VIDEO, transcript)
    lines = summary.splitlines()
    assert lines[0] == VISUAL_SIGNAL_HEADER
    first = [line for line in lines if "00:10 - 00:20" in line]
    assert first
    # 3 of the 5 samples inside [10,20) carry a face -> 60%.
    assert "face=60%" in first[0]
    assert "cuts=0" in first[0]
    # No line for the second span: it has zero decoded samples in range.
    assert not any("00:40 - 00:50" in line for line in lines)


def test_scene_cuts_are_counted_per_span(monkeypatch):
    track = _track([(t, 320.0, 0.02) for t in (10, 12, 14, 16, 18, 20)])
    monkeypatch.setattr(
        visual_signals, "analyze_vertical_clip", lambda p: (track, [12.5, 13.9])
    )
    transcript = "[00:10 - 00:20] first span"
    summary = build_visual_signal_summary(VIDEO, transcript)
    assert "cuts=2" in summary


def test_short_spans_are_skipped(monkeypatch):
    track = _track([(t, 320.0, 0.02) for t in (10, 12, 14)])
    monkeypatch.setattr(visual_signals, "analyze_vertical_clip", lambda p: (track, []))
    transcript = "[00:10 - 00:14] four-second span"
    assert build_visual_signal_summary(VIDEO, transcript) == ""


def test_framing_stable_vs_moving(monkeypatch):
    stable = _track([(t, 320.0, 0.02) for t in (10, 12, 14, 16, 18)])
    monkeypatch.setattr(
        visual_signals, "analyze_vertical_clip", lambda p: (stable, [])
    )
    summary = build_visual_signal_summary(VIDEO, "[00:10 - 00:20] a span")
    assert "framing=stable" in summary

    moving = _track([(t, x, 0.02) for t, x in ((10, 100.0), (12, 900.0), (14, 100.0), (16, 900.0), (18, 100.0))])
    monkeypatch.setattr(
        visual_signals, "analyze_vertical_clip", lambda p: (moving, [])
    )
    summary = build_visual_signal_summary(VIDEO, "[00:10 - 00:20] a span")
    assert "framing=moving" in summary


def test_decode_failure_returns_empty(monkeypatch):
    def boom(_path):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(visual_signals, "analyze_vertical_clip", boom)
    assert build_visual_signal_summary(VIDEO, "[00:10 - 00:20] a span") == ""
