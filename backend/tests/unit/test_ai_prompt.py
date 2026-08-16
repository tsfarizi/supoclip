from types import SimpleNamespace

import pytest
from pydantic_ai.models.ollama import OllamaModel

from src.ai import (
    IDEAL_CLIP_MAX_SECONDS,
    IDEAL_CLIP_MIN_SECONDS,
    MIN_ACCEPTED_CLIP_SECONDS,
    TranscriptSegment,
    HookSelection,
    _build_transcript_model,
    _choose_repaired_bounds,
    _extract_transcript_text,
    _format_transcript_timestamp,
    _get_missing_llm_key_error,
    _parse_transcript_spans,
    _parse_transcript_timestamp_seconds,
    _validate_or_repair_hook_selection,
    build_transcript_analysis_prompt,
    transcript_analysis_system_prompt,
)


def test_system_prompt_enforces_grounding_rules():
    assert "extraction and ranking, not creative rewriting" in (
        transcript_analysis_system_prompt
    )
    assert "Never invent facts, tone, context, or transitions" in (
        transcript_analysis_system_prompt
    )
    assert "Each selected segment must map to one contiguous range" in (
        transcript_analysis_system_prompt
    )
    assert "Do not judge, moralize, or downgrade a segment" in (
        transcript_analysis_system_prompt
    )
    assert f"{IDEAL_CLIP_MIN_SECONDS}-{IDEAL_CLIP_MAX_SECONDS} seconds" in (
        transcript_analysis_system_prompt
    )
    assert "Bad picks include intros" in transcript_analysis_system_prompt
    assert "Return valid JSON only" in transcript_analysis_system_prompt
    assert "Do not use \"segment\" as an output field. Use \"text\"." in (
        transcript_analysis_system_prompt
    )
    assert '"hook_selection"' in transcript_analysis_system_prompt
    assert "1-5 seconds inclusive" in transcript_analysis_system_prompt


def test_hook_selection_schema_requires_source_range_evidence_and_score_bounds():
    hook = HookSelection(
        hook_start_time="00:10",
        hook_end_time="00:11",
        transcript_evidence="A source line",
        reasoning="It creates curiosity",
        hook_score=25,
    )

    assert hook.hook_score == 25


def test_hook_selection_schema_rejects_score_outside_contract():
    with pytest.raises(ValueError):
        HookSelection(
            hook_start_time="00:10",
            hook_end_time="00:11",
            transcript_evidence="A source line",
            reasoning="reason",
            hook_score=26,
        )


def test_invalid_one_second_boundary_hooks_are_repaired_from_transcript():
    segment = TranscriptSegment(
        start_time="00:20",
        end_time="00:40",
        text="Main segment",
        hook_selection=HookSelection(
            hook_start_time="00:10",
            hook_end_time="00:16",
            transcript_evidence="invented",
            reasoning="bad duration",
            hook_score=7,
        ),
    )
    spans = _parse_transcript_spans("[00:14 - 00:19] Grounded setup\n[00:20 - 00:40] Main")

    repaired = _validate_or_repair_hook_selection(segment, spans, 20)

    assert repaired is not None
    assert repaired.hook_start_time == "00:14"
    assert repaired.hook_end_time == "00:19"
    assert repaired.transcript_evidence == "Grounded setup"


def test_valid_five_second_hook_is_clamped_to_transcript_evidence():
    segment = TranscriptSegment(
        start_time="00:20", end_time="00:40", text="Main",
        hook_selection=HookSelection(
            hook_start_time="00:15", hook_end_time="00:20",
            transcript_evidence="wrong", reasoning="reason", hook_score=3,
        ),
    )
    spans = _parse_transcript_spans("[00:15 - 00:20] Exact setup\n[00:20 - 00:40] Main")

    validated = _validate_or_repair_hook_selection(segment, spans, 20)

    assert validated is not None
    assert validated.transcript_evidence == "Exact setup"


def test_build_transcript_analysis_prompt_requires_transcript_fidelity():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line"
    )

    assert "Do not fabricate or embellish content." in prompt
    assert "Do not merge separate non-contiguous moments into one segment." in prompt
    assert "If there is a tradeoff between \"viral\" and \"accurate\", choose accuracy." in prompt
    assert "Do not reject or penalize a segment simply because of the subject matter" in prompt
    assert "end_time MUST land at the end of a complete sentence" in prompt
    assert f"Most selected clips should be {IDEAL_CLIP_MIN_SECONDS}-{IDEAL_CLIP_MAX_SECONDS} seconds." in prompt
    assert "viewer would understand and care without seeing the rest" in prompt
    assert "Return one valid JSON object and nothing else." in prompt
    assert "No Markdown, headings, bullets, code fences" in prompt
    assert "[00:12 - 00:21] A strong opening line" in prompt


def test_build_transcript_analysis_prompt_mentions_broll_only_when_enabled():
    without_broll = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line"
    )
    with_broll = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line",
        include_broll=True,
    )

    assert "B-roll opportunities" not in without_broll
    assert "B-roll opportunities" in with_broll


def test_ollama_llm_builds_native_ollama_model():
    runtime_config = SimpleNamespace(
        llm="ollama:gpt-oss:20b",
        ollama_api_key=None,
        resolve_ollama_base_url=lambda: "http://ollama.example/v1",
    )

    model = _build_transcript_model(runtime_config)

    assert isinstance(model, OllamaModel)
    assert model.model_name == "gpt-oss:20b"
    assert model.base_url == "http://ollama.example/v1/"


def test_parse_transcript_timestamp_supports_minute_and_hour_formats():
    assert _parse_transcript_timestamp_seconds("02:35") == 155
    assert _parse_transcript_timestamp_seconds("01:02:35") == 3755
    assert _format_transcript_timestamp(155) == "02:35"
    assert _format_transcript_timestamp(3755) == "01:02:35"
    assert MIN_ACCEPTED_CLIP_SECONDS == 15


def test_transcript_span_helpers_repair_near_miss_durations():
    spans = _parse_transcript_spans(
        "\n".join(
            [
                "[00:00 - 00:10] Setup",
                "[00:10 - 00:24] Short highlight",
                "[00:24 - 00:36] Payoff",
                "[00:36 - 01:20] Too much background",
            ]
        )
    )

    assert _extract_transcript_text(spans, 10, 36) == "Short highlight Payoff"
    assert _choose_repaired_bounds(spans, 10, 24) == (10, 36)
    assert _choose_repaired_bounds(spans, 0, 80) == (0, 36)


def test_transcript_segment_normalizes_percent_relevance_score():
    segment = TranscriptSegment(
        start_time="00:00",
        end_time="00:30",
        text="A complete standalone moment with useful context.",
        relevance_score=100,
    )

    assert segment.relevance_score == 1.0


def test_llm_validation_rejects_unsupported_or_incomplete_model_names():
    runtime_config = SimpleNamespace(
        google_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
    )

    assert "Unsupported LLM provider" in _get_missing_llm_key_error(
        "local:model", runtime_config
    )
    assert "missing a model name" in _get_missing_llm_key_error(
        "ollama:", runtime_config
    )
    assert _get_missing_llm_key_error("ollama:gpt-oss:20b", runtime_config) is None
