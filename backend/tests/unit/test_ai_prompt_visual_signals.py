"""Falsification tests for the visual-signals prompt section (src/ai.py).

Contracts: passing visual_signals adds the "Deterministic visual signals"
section to the analysis prompt; omitting it leaves the prompt identical to the
pre-visual-signals layout; both clip_signals and visual_signals can coexist.
"""

from src.ai import build_transcript_analysis_prompt


def test_visual_signals_section_is_added():
    prompt = build_transcript_analysis_prompt(
        "[00:00 - 00:10] hello", visual_signals="fake-visual-block"
    )
    assert "Additional deterministic visual signals from video analysis" in prompt
    assert "fake-visual-block" in prompt


def test_no_visual_signals_section_when_omitted():
    prompt = build_transcript_analysis_prompt("[00:00 - 00:10] hello")
    assert "Additional deterministic visual signals from video analysis" not in prompt


def test_clip_and_visual_signals_coexist():
    prompt = build_transcript_analysis_prompt(
        "[00:00 - 00:10] hello",
        clip_signals="clip-block",
        visual_signals="visual-block",
    )
    assert "clip-block" in prompt
    assert "visual-block" in prompt
    assert prompt.index("clip-block") < prompt.index("visual-block")


def test_transcript_is_always_included():
    prompt = build_transcript_analysis_prompt(
        "[00:00 - 00:10] hello", visual_signals="fake"
    )
    assert "[00:00 - 00:10] hello" in prompt
