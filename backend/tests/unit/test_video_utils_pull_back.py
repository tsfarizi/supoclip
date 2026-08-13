"""Falsification tests for the sentence pull-back behaviour in
extend_keep_ranges_to_sentence_boundary (src/video_utils.py).

Contract (U8): when no sentence boundary is reachable inside the extension cap,
pull_back_to_complete_sentence pulls the clip end BACKWARD to the last complete
sentence before the original end, but only when the resulting final range is at
least min_duration_seconds long. Otherwise the historical end is preserved and
the default (pull_back=False) reproduces the historical behaviour.
"""

import pytest
from unittest.mock import patch

from src import video_utils

# Words (ms timing): 8.0s..11.0s continuous speech with one sentence ending at
# 10.2s, then more speech continuing well past any extension cap.
WORDS = [
    {"text": "This", "start": 8000, "end": 8300, "confidence": 0.99},
    {"text": "is", "start": 8300, "end": 8500, "confidence": 0.99},
    {"text": "one", "start": 8500, "end": 8800, "confidence": 0.99},
    {"text": "sentence.", "start": 8800, "end": 9200, "confidence": 0.99},
    {"text": "Another", "start": 9200, "end": 9500, "confidence": 0.99},
    {"text": "thought", "start": 9500, "end": 9800, "confidence": 0.99},
    {"text": "continues", "start": 9800, "end": 10200, "confidence": 0.99},
    {"text": "past", "start": 10200, "end": 10500, "confidence": 0.99},
    {"text": "the", "start": 10500, "end": 10700, "confidence": 0.99},
    {"text": "end.", "start": 10700, "end": 11000, "confidence": 0.99},
]


def _cache(video_path):
    video_path.with_suffix(".transcript_cache.json").write_text(
        '{"version": 2, "words": ' + __import__("json").dumps(WORDS) + ', "utterances": [], "text": ""}',
        encoding="utf-8",
    )


def test_pull_back_to_last_complete_sentence(tmp_path):
    """End lands mid-sentence at 10.6s; the 0.5s extension cap lands at 11.1s
    which is not a sentence end, so the end is pulled back to the sentence
    boundary at 9.2s when the resulting clip stays long enough
    (total 2.6s -> 1.2s >= min 1.0s)."""
    video_path = tmp_path / "source.mp4"
    _cache(video_path)

    with patch("src.video_utils.ffprobe_duration", return_value=60.0):
        ranges = video_utils.extend_keep_ranges_to_sentence_boundary(
            video_path,
            [(8.0, 10.6)],
            max_extension_seconds=0.5,
            pull_back_to_complete_sentence=True,
            min_duration_seconds=1.0,
        )

    assert ranges == [(8.0, 9.2)]


def test_pull_back_respects_min_duration(tmp_path):
    """Pulling back to 9.2s would leave the clip at 1.2s, below the 5.0s
    minimum; the historical end (10.6s) is preserved."""
    video_path = tmp_path / "source.mp4"
    _cache(video_path)

    with patch("src.video_utils.ffprobe_duration", return_value=60.0):
        ranges = video_utils.extend_keep_ranges_to_sentence_boundary(
            video_path,
            [(8.0, 10.6)],
            max_extension_seconds=0.0,
            pull_back_to_complete_sentence=True,
            min_duration_seconds=5.0,
        )

    assert ranges == [(8.0, 10.6)]


def test_default_no_pull_back_reproduces_historical(tmp_path):
    """Without pull_back enabled the historical end is kept when no extension
    is reachable."""
    video_path = tmp_path / "source.mp4"
    _cache(video_path)

    with patch("src.video_utils.ffprobe_duration", return_value=60.0):
        ranges = video_utils.extend_keep_ranges_to_sentence_boundary(
            video_path,
            [(8.0, 10.6)],
            max_extension_seconds=0.0,
        )

    assert ranges == [(8.0, 10.6)]


def test_extension_still_preferred_over_pull_back(tmp_path):
    """When a sentence boundary is reachable by extension, the end moves
    forward and pull-back is never used."""
    video_path = tmp_path / "source.mp4"
    _cache(video_path)

    with patch("src.video_utils.ffprobe_duration", return_value=60.0):
        ranges = video_utils.extend_keep_ranges_to_sentence_boundary(
            video_path,
            [(8.0, 8.5)],
            max_extension_seconds=3.0,
            pull_back_to_complete_sentence=True,
        )

    # Extends to the sentence end + padding (11.0 + 0.35).
    assert ranges[0][1] == pytest.approx(11.35)


def test_missing_transcript_leaves_ranges_unchanged(tmp_path):
    video_path = tmp_path / "source.mp4"  # no cache sidecar

    ranges = video_utils.extend_keep_ranges_to_sentence_boundary(
        video_path,
        [(8.0, 10.6)],
        pull_back_to_complete_sentence=True,
    )

    assert ranges == [(8.0, 10.6)]
