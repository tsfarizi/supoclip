"""
Falsification tests for snap_segment_end_to_sentence in src/video_utils.py.

Covered contracts:
  1. End mid-sentence with a sentence closer inside the window -> snapped
     forward to the closer's word end.
  2. End already at a sentence boundary (closer's word end) -> unchanged.
  3. No sentence closer inside the window -> unchanged.
  4. Empty transcript words -> unchanged.
  5. Negative / out-of-range / NaN / non-numeric end -> unchanged (no crash).
  6. A closer entirely before the end never snaps backward.
  7. With multiple closers in the window the earliest word end wins.
  8. Malformed word entries (missing/non-numeric timings) are skipped safely.
  9. max_window_seconds widens/narrows the search window.
"""

from __future__ import annotations

import math

from src.video_utils import snap_segment_end_to_sentence

SENTENCE_WORDS = [
    {"text": "This", "start": 0.0, "end": 0.4},
    {"text": "is", "start": 0.4, "end": 0.8},
    {"text": "a", "start": 0.8, "end": 1.0},
    {"text": "great", "start": 1.0, "end": 1.5},
    {"text": "story.", "start": 1.5, "end": 2.2},
]


class TestSnapSegmentEndToSentence:
    def test_end_mid_sentence_snaps_to_next_closer(self):
        # 1.2s lands inside "great" (1.0-1.5); "story." closes the sentence at 2.2.
        assert snap_segment_end_to_sentence(SENTENCE_WORDS, 1.2) == 2.2

    def test_end_at_closer_start_snaps_forward_to_include_closer(self):
        # 1.5s is the start of the closing word; the sentence is not finished yet.
        assert snap_segment_end_to_sentence(SENTENCE_WORDS, 1.5) == 2.2

    def test_end_at_sentence_boundary_is_unchanged(self):
        # 2.2s is exactly the closer's word end; no extension needed.
        assert snap_segment_end_to_sentence(SENTENCE_WORDS, 2.2) == 2.2

    def test_no_closer_within_window_is_unchanged(self):
        words = [
            {"text": "This", "start": 0.0, "end": 0.4},
            {"text": "is", "start": 0.4, "end": 0.8},
            {"text": "fine", "start": 0.8, "end": 1.2},
            {"text": "for", "start": 1.2, "end": 1.6},
            {"text": "now.", "start": 6.0, "end": 7.0},
        ]
        # "now." starts at 6.0, far beyond end + 3.0.
        assert snap_segment_end_to_sentence(words, 1.2) == 1.2

    def test_empty_transcript_is_unchanged(self):
        assert snap_segment_end_to_sentence([], 5.0) == 5.0

    def test_negative_end_is_unchanged(self):
        # No word can be a closer at/after a negative end with start inside the window.
        assert snap_segment_end_to_sentence(SENTENCE_WORDS, -5.0) == -5.0

    def test_end_beyond_all_words_is_unchanged(self):
        assert snap_segment_end_to_sentence(SENTENCE_WORDS, 999.0) == 999.0

    def test_nan_end_is_unchanged(self):
        result = snap_segment_end_to_sentence(SENTENCE_WORDS, float("nan"))
        assert math.isnan(result)

    def test_non_numeric_end_is_unchanged(self):
        assert snap_segment_end_to_sentence(SENTENCE_WORDS, None) is None

    def test_closer_before_end_never_snaps_backward(self):
        words = [
            {"text": "done.", "start": 1.0, "end": 2.0},
            {"text": "more", "start": 2.0, "end": 3.0},
        ]
        # "done." ends at 2.0, before the 5.0 end; it must not pull the end back.
        assert snap_segment_end_to_sentence(words, 5.0) == 5.0

    def test_earliest_closer_within_window_wins(self):
        words = [
            {"text": "one.", "start": 0.0, "end": 1.0},
            {"text": "two.", "start": 1.0, "end": 2.0},
        ]
        assert snap_segment_end_to_sentence(words, 0.5) == 1.0

    def test_malformed_word_entries_are_skipped_safely(self):
        words = [
            {"text": "ok.", "start": 0.0},  # missing end
            {"text": "bad", "start": "x", "end": 2.0},  # non-numeric start
            {"text": "fine.", "start": 2.0, "end": 3.0},
        ]
        assert snap_segment_end_to_sentence(words, 1.0) == 3.0

    def test_custom_window_widens_the_search(self):
        words = [
            {"text": "This", "start": 0.0, "end": 0.4},
            {"text": "is", "start": 0.4, "end": 0.8},
            {"text": "fine", "start": 0.8, "end": 1.2},
            {"text": "for", "start": 1.2, "end": 1.6},
            {"text": "now.", "start": 6.0, "end": 7.0},
        ]
        # Outside the default 3.0s window but inside a custom 6.0s window.
        assert snap_segment_end_to_sentence(words, 1.2) == 1.2
        assert snap_segment_end_to_sentence(words, 1.2, max_window_seconds=6.0) == 7.0
