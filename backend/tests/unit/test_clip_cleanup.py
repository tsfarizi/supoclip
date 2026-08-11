"""
Falsification tests for clip cleanup settings normalization.

Covered contracts (src/clip_cleanup.py):
  1. normalize_pause_threshold_ms: int-coerce with [250, 3000] clamp; invalid
     input falls back to a configurable default.
  2. normalize_filtered_words: comma-string or list input, case-folded,
     whitespace-collapsed, deduplicated in first-seen order; non-string items
     dropped; any other type -> [].
  3. normalize_clip_cleanup_settings: bool/coercion of flags, normalized
     threshold and filtered words.
  4. clip_cleanup_enabled: truthiness across the three cleanup knobs; missing/
     falsy settings disable cleanup.
"""

from __future__ import annotations

from src.clip_cleanup import (
    DEFAULT_FILTERED_WORDS,
    DEFAULT_PAUSE_THRESHOLD_MS,
    clip_cleanup_enabled,
    normalize_clip_cleanup_settings,
    normalize_filtered_words,
    normalize_pause_threshold_ms,
)


class TestNormalizePauseThresholdMs:
    def test_in_range_value_is_kept(self):
        assert normalize_pause_threshold_ms(500) == 500
        assert normalize_pause_threshold_ms(900) == 900

    def test_integer_string_is_parsed(self):
        assert normalize_pause_threshold_ms("900") == 900
        assert normalize_pause_threshold_ms("250") == 250
        assert normalize_pause_threshold_ms("3000") == 3000

    def test_below_min_is_clamped_to_250(self):
        assert normalize_pause_threshold_ms(0) == 250
        assert normalize_pause_threshold_ms(-100) == 250
        assert normalize_pause_threshold_ms(249) == 250

    def test_above_max_is_clamped_to_3000(self):
        assert normalize_pause_threshold_ms(4000) == 3000
        assert normalize_pause_threshold_ms(10**9) == 3000

    def test_exact_boundaries_survive(self):
        assert normalize_pause_threshold_ms(250) == 250
        assert normalize_pause_threshold_ms(3000) == 3000

    def test_non_numeric_input_falls_back_to_default(self):
        assert normalize_pause_threshold_ms(None) == DEFAULT_PAUSE_THRESHOLD_MS
        assert normalize_pause_threshold_ms("abc") == DEFAULT_PAUSE_THRESHOLD_MS
        assert normalize_pause_threshold_ms("") == DEFAULT_PAUSE_THRESHOLD_MS
        assert normalize_pause_threshold_ms([]) == DEFAULT_PAUSE_THRESHOLD_MS
        assert normalize_pause_threshold_ms({}) == DEFAULT_PAUSE_THRESHOLD_MS

    def test_float_truncates_but_float_string_does_not_parse(self):
        # int(350.7) truncates; int("350.7") raises -> default.
        assert normalize_pause_threshold_ms(350.7) == 350
        assert normalize_pause_threshold_ms("350.7") == DEFAULT_PAUSE_THRESHOLD_MS

    def test_custom_default_is_used_on_invalid_input(self):
        assert normalize_pause_threshold_ms("nope", default=700) == 700
        assert normalize_pause_threshold_ms(500, default=700) == 500


class TestNormalizeFilteredWords:
    def test_comma_string_is_split(self):
        assert normalize_filtered_words("um, uh") == ["um", "uh"]

    def test_case_is_folded_and_whitespace_collapsed(self):
        assert normalize_filtered_words("  UM , Uh  ,um") == ["um", "uh"]

    def test_multiword_phrase_survives_whitespace_collapse(self):
        assert normalize_filtered_words("you   know, i  mean") == [
            "you know",
            "i mean",
        ]

    def test_duplicates_are_dropped_keeping_first_seen(self):
        assert normalize_filtered_words("um,uh,um,uh") == ["um", "uh"]
        assert normalize_filtered_words("you know,you   know") == ["you know"]

    def test_empty_string_returns_empty_list(self):
        assert normalize_filtered_words("") == []
        assert normalize_filtered_words(",") == []
        assert normalize_filtered_words("um,,,uh,") == ["um", "uh"]

    def test_list_input_is_normalized(self):
        assert normalize_filtered_words([" Um ", "uh", "UM"]) == ["um", "uh"]

    def test_non_string_items_are_skipped(self):
        assert normalize_filtered_words([1, "um", None, 3.5, "uh"]) == ["um", "uh"]

    def test_non_str_non_list_input_returns_empty_list(self):
        assert normalize_filtered_words(None) == []
        assert normalize_filtered_words(42) == []
        assert normalize_filtered_words({"a": 1}) == []

    def test_default_words_are_recognized_after_normalization(self):
        normalized = normalize_filtered_words(",".join(DEFAULT_FILTERED_WORDS))
        assert normalized == DEFAULT_FILTERED_WORDS


class TestNormalizeClipCleanupSettings:
    def test_defaults_are_fully_normalized(self):
        settings = normalize_clip_cleanup_settings()
        assert settings == {
            "cut_long_pauses": False,
            "pause_threshold_ms": DEFAULT_PAUSE_THRESHOLD_MS,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    def test_threshold_and_words_are_normalized(self):
        settings = normalize_clip_cleanup_settings(
            pause_threshold_ms="0", filtered_words="um,UH,um"
        )
        assert settings["pause_threshold_ms"] == 250
        assert settings["filtered_words"] == ["um", "uh"]

    def test_flag_coercion_is_python_truthiness(self):
        # Contract freeze: any non-empty string (including "false"/"0") is
        # truthy, only "", None, 0 are falsy. Callers passing raw query-param
        # strings must pre-coerce if they expect literal "false".
        settings = normalize_clip_cleanup_settings(
            cut_long_pauses="false", remove_filler_words=""
        )
        assert settings["cut_long_pauses"] is True
        assert settings["remove_filler_words"] is False
        assert settings["cut_long_pauses"] is bool("false")

    def test_truthy_and_falsy_flag_values(self):
        assert normalize_clip_cleanup_settings(cut_long_pauses=True)[
            "cut_long_pauses"
        ]
        assert normalize_clip_cleanup_settings(cut_long_pauses=1)[
            "cut_long_pauses"
        ]
        assert normalize_clip_cleanup_settings(cut_long_pauses=False)[
            "cut_long_pauses"
        ] is False
        assert normalize_clip_cleanup_settings(cut_long_pauses=None)[
            "cut_long_pauses"
        ] is False


class TestClipCleanupEnabled:
    def test_none_or_empty_disables_cleanup(self):
        assert clip_cleanup_enabled(None) is False
        assert clip_cleanup_enabled({}) is False

    def test_cut_long_pauses_enables(self):
        assert clip_cleanup_enabled({"cut_long_pauses": True}) is True

    def test_remove_filler_words_enables(self):
        assert clip_cleanup_enabled({"remove_filler_words": True}) is True

    def test_non_empty_filtered_words_enables(self):
        assert clip_cleanup_enabled({"filtered_words": ["um"]}) is True

    def test_all_falsy_disables(self):
        assert (
            clip_cleanup_enabled(
                {
                    "cut_long_pauses": False,
                    "remove_filler_words": False,
                    "filtered_words": [],
                }
            )
            is False
        )

    def test_unrelated_keys_do_not_enable(self):
        assert clip_cleanup_enabled({"unrelated": 1}) is False

    def test_normalized_settings_round_trip(self):
        settings = normalize_clip_cleanup_settings(
            cut_long_pauses=True, filtered_words="um"
        )
        assert clip_cleanup_enabled(settings) is True
        assert clip_cleanup_enabled(normalize_clip_cleanup_settings()) is False
