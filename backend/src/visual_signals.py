"""
Deterministic visual hints for the transcript-analysis LLM.

The LLM that picks clip segments is otherwise blind to visuals. This module
derives a compact per-span summary (face presence, scene cuts, framing
stability) from a single low-cost decode pass over the source video, so
segment ranking can avoid spans that read well but look bad (e.g. mid-span
scene cuts, no visible speaker, unstable framing). The output is a plain-text
block injected into the analysis prompt exactly like build_clip_signal_summary.

The decode pass is the same one the vertical reframe uses (analyze_vertical_clip);
frame-budgeting inside that function bounds the work on long videos.
"""

from pathlib import Path
from typing import List, Optional, Tuple
import logging

from .video_utils import analyze_vertical_clip, parse_transcript_lines

logger = logging.getLogger(__name__)

VISUAL_SIGNAL_HEADER = "Deterministic visual signals (per transcript span):"
MIN_SPAN_SECONDS = 8.0
MAX_SPANS_IN_SUMMARY = 40


def _span_stats(
    track: List[Tuple[float, Optional[float], float]],
    scene_cuts: List[float],
    start: float,
    end: float,
) -> Tuple[int, int, Optional[float]]:
    """Return (sample_count, scene_cut_count, x_span_px) for a time window.

    Only samples strictly inside [start, end) are counted. x_span is the range
    of detected face centers within the window, or None when fewer than two
    faces were detected.
    """
    samples = [s for s in track if start <= s[0] < end]
    if not samples:
        return 0, 0, None

    detected_x = [s[1] for s in samples if s[1] is not None]
    x_span = None
    if len(detected_x) >= 2:
        x_span = max(detected_x) - min(detected_x)

    cuts = [c for c in scene_cuts if start < c < end]
    return len(samples), len(cuts), x_span


def build_visual_signal_summary(video_path: Path, transcript: str) -> str:
    """Build visual hints per transcript span, or '' when nothing is usable."""
    try:
        lines = parse_transcript_lines(transcript)
        if not lines:
            return ""
    except Exception as exc:
        logger.warning("Visual signal transcript parse failed: %s", exc)
        return ""

    try:
        track, scene_cuts = analyze_vertical_clip(video_path)
    except Exception as exc:
        logger.warning("Visual signal decode failed: %s", exc)
        return ""
    if not track:
        return ""

    # The largest observed face center approximates the source width; used to
    # express framing stability as a fraction instead of raw pixels.
    observed_width = max(
        (x for _, x, _ in track if x is not None), default=1.0
    ) or 1.0

    summary_lines = [VISUAL_SIGNAL_HEADER]
    for line in lines[:MAX_SPANS_IN_SUMMARY]:
        start = float(line["start"])
        end = float(line["end"])
        if end - start < MIN_SPAN_SECONDS:
            continue

        sample_count, cut_count, x_span = _span_stats(track, scene_cuts, start, end)
        if sample_count == 0:
            continue

        detected = sum(
            1
            for t, x, area in track
            if start <= t < end and x is not None and area > 0
        )
        face_pct = round(100 * detected / sample_count)
        framing = "unknown"
        if x_span is not None:
            framing = "stable" if x_span < 0.15 * observed_width else "moving"

        summary_lines.append(
            f"- [{line['start_label']} - {line['end_label']}] "
            f"face={face_pct}% cuts={cut_count} framing={framing}"
        )

    if len(summary_lines) == 1:
        return ""
    return "\n".join(summary_lines)
