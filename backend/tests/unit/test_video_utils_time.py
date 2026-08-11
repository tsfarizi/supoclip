"""
Falsification tests for pure time/format helpers in src/video_utils.py.

Covered contracts:
  1. parse_timestamp_to_seconds: "MM:SS" and "HH:MM:SS" conversion, plain
     seconds fallback, 0.0 on parse failure. Minutes/seconds are NOT range
     validated (e.g. "00:90" parses to 90s).
  2. seconds_to_mmss: integer-second rounding, MM:SS formatting, negative
     input clamped to 0.
  3. VALID_OUTPUT_FORMATS: the closed set of accepted output formats.

These helpers are pure; importing video_utils pulls heavy deps (cv2, aai,
httpx) which are already available to the existing video_utils test files.
"""

from __future__ import annotations

import pytest

from src.video_utils import (
    VALID_OUTPUT_FORMATS,
    parse_timestamp_to_seconds,
    seconds_to_mmss,
)


class TestParseTimestampToSeconds:
    def test_mmss_format(self):
        assert parse_timestamp_to_seconds("01:05") == 65.0
        assert parse_timestamp_to_seconds("00:10") == 10.0
        assert parse_timestamp_to_seconds("1:2") == 62.0

    def test_hhmmss_format(self):
        assert parse_timestamp_to_seconds("1:02:03") == 3723.0
        assert parse_timestamp_to_seconds("0:00:45") == 45.0

    def test_plain_seconds(self):
        assert parse_timestamp_to_seconds("90") == 90.0
        assert parse_timestamp_to_seconds("90.5") == 90.5
        assert parse_timestamp_to_seconds("0") == 0.0

    def test_whitespace_is_trimmed(self):
        assert parse_timestamp_to_seconds("  00:10  ") == 10.0

    def test_out_of_range_minutes_and_seconds_are_not_validated(self):
        # Contract freeze: parts are parsed with int() only; 90s / 60s values
        # silently overflow their natural range.
        assert parse_timestamp_to_seconds("00:90") == 90.0
        assert parse_timestamp_to_seconds("5:60") == 360.0

    def test_invalid_inputs_return_zero(self):
        assert parse_timestamp_to_seconds("") == 0.0
        assert parse_timestamp_to_seconds("abc") == 0.0
        assert parse_timestamp_to_seconds("12:34:56:78") == 0.0
        assert parse_timestamp_to_seconds("a:b") == 0.0
        assert parse_timestamp_to_seconds("12:34:56.7") == 0.0


class TestSecondsToMmss:
    def test_zero(self):
        assert seconds_to_mmss(0) == "00:00"

    def test_under_a_minute(self):
        assert seconds_to_mmss(59) == "00:59"

    def test_exact_minute_rolls_over(self):
        assert seconds_to_mmss(60) == "01:00"

    def test_minutes_and_seconds(self):
        assert seconds_to_mmss(125) == "02:05"

    def test_hour_lengths_use_unbounded_minutes(self):
        assert seconds_to_mmss(3599) == "59:59"
        assert seconds_to_mmss(3600) == "60:00"

    def test_fractional_seconds_round_half_up(self):
        assert seconds_to_mmss(59.4) == "00:59"
        assert seconds_to_mmss(59.6) == "01:00"

    def test_negative_seconds_clamp_to_zero(self):
        assert seconds_to_mmss(-5) == "00:00"
        assert seconds_to_mmss(-0.1) == "00:00"


class TestValidOutputFormats:
    def test_exact_set_of_supported_formats(self):
        assert VALID_OUTPUT_FORMATS == {
            "vertical",
            "vertical_pan",
            "vertical_split",
            "original",
        }

    def test_membership(self):
        for fmt in ("vertical", "vertical_pan", "vertical_split", "original"):
            assert fmt in VALID_OUTPUT_FORMATS
        assert "invalid" not in VALID_OUTPUT_FORMATS
        assert "" not in VALID_OUTPUT_FORMATS
