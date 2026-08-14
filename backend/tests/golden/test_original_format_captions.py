"""Golden end-to-end harness: original-format WHITE-CANVAS clip with captions.

Verifies the NEW original-format contract through the REAL ffmpeg binary — the
render pass is never mocked. The only test-side wrapper around
`video_utils.run_ffmpeg_command` substitutes the resolved ffmpeg/ffprobe
executables for the bare names (matching `test_clip_quality.py`).

Contracts under falsification
-----------------------------
1. `create_optimized_clip(..., output_format="original")` renders a
   1080x1920 canvas. A 640x360 source is fit full-frame
   (`force_original_aspect_ratio=decrease`, even-truncated) and centred on a
   WHITE background (`pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white`). Both a
   captioned render (A) and a captionless render (B) must probe at 1080x1920 —
   the old "keep source size" behaviour (and the stream-copy fast path) is GONE.
2. A — valid transcript sidecar + `add_subtitles=True` — burns word-synced
   captions INTO the output frame. At t=1.0s the bottom band y in [1264, 1920)
   (below the 1080x608 fitted video; 656px of white canvas) must contain a
   significant number of NON-white pixels: the active word "supo" is yellow
   (#FFE000) and every word carries a black outline, so the caption area is
   anything but the surrounding white canvas.
3. B — `add_subtitles=False` — must be a bare white-canvas render: the same
   bottom band is pure white (near-zero non-white pixels). This is the control:
   on the NEW contract B is NOT the untouched source (fast path removed); it is
   a real 1080x1920 white-canvas encode with nothing burned into the frame.

Honesty notes (read before trusting a red/green)
-----------------------------------------------
* The ASS burn happens inside `render_reframed_clip_ffmpeg` via the
  `subtitles` filter (libass). If the bundled ffmpeg lacks libass the render
  returns False and the test FAILS honestly — it is never masked.
* The synthetic clip is solid black + a sine tone: every non-white pixel in the
  bottom band of the A render is caption text/outline, there is no scene
  content to confuse the count.
* The white canvas itself is BRIGHT — this harness never asserts on "bright
  pixels"; it asserts on NON-white pixels (min(r,g,b) < 245) inside the bottom
  band only.
* Frame parsing is PURE Python (bytes/struct over an rgb24 pipe) — PIL and
  numpy are deliberately not used.
* The transcript sidecar words end at exactly 2500 ms with a sentence-ending
  token, so `extend_keep_ranges_to_sentence_boundary` keeps the clip range
  [(0.5, 2.5)] unchanged (early return on a sentence-end boundary) — no
  timeline drift between the caption events and the extracted frame.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from src import video_utils


# ---------------------------------------------------------------------------
# ffmpeg resolution (skip, never fail, when the toolchain is missing)
# ---------------------------------------------------------------------------
def _resolve_executable(name: str):
    """Locate an ffmpeg-family executable via Config().ffmpeg_bin_dir then PATH."""
    from src.config import Config

    bin_dir = Config().ffmpeg_bin_dir
    suffix = ".exe" if os.name == "nt" else ""
    if bin_dir:
        candidate = Path(bin_dir) / f"{name}{suffix}"
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


@pytest.fixture(scope="session")
def ffmpeg_toolchain():
    """Resolve the real ffmpeg/ffprobe binaries; SKIP the golden suite when absent."""
    ffmpeg_bin = _resolve_executable("ffmpeg")
    ffprobe_bin = _resolve_executable("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip(
            "ffmpeg/ffprobe not found via Config().ffmpeg_bin_dir or PATH "
            f"(ffmpeg={ffmpeg_bin!r}, ffprobe={ffprobe_bin!r}) — golden render tests skipped"
        )
    return {"ffmpeg": ffmpeg_bin, "ffprobe": ffprobe_bin}


# ---------------------------------------------------------------------------
# Synthetic fixture (session-scoped, real ffmpeg, once)
# ---------------------------------------------------------------------------
def _run(cmd, timeout=300):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, f"fixture generation failed:\n{result.stderr[-1500:]}"
    return result


@pytest.fixture(scope="session")
def caption_clip(ffmpeg_toolchain, tmp_path_factory):
    """3s 640x360 black video + 440Hz sine, WITH a valid transcript sidecar.

    The sidecar words span 500-2500 ms (milliseconds, as written by
    `cache_transcript_data`). The final word ends a sentence exactly at the
    2500 ms clip end, so the sentence-boundary extension leaves the
    keep-range [(0.5, 2.5)] untouched and the caption timeline is stable:
    the word "supo" (source 1300-1700 ms) is active at output t=1.0s.
    """
    work_dir = tmp_path_factory.mktemp("golden_original_captions")
    video = work_dir / "synthetic_black.mp4"
    _run(
        [
            ffmpeg_toolchain["ffmpeg"], "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:d=3:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(video),
        ]
    )
    assert video.exists() and video.stat().st_size > 0

    sidecar = video.with_suffix(".transcript_cache.json")
    sidecar.write_text(
        '{"version":2,"words":['
        '{"text":"hello","start":500,"end":900,"confidence":1.0},'
        '{"text":"world","start":900,"end":1300,"confidence":1.0},'
        '{"text":"supo","start":1300,"end":1700,"confidence":1.0},'
        '{"text":"clip","start":1700,"end":2100,"confidence":1.0},'
        '{"text":"rules.","start":2100,"end":2500,"confidence":1.0}'
        '],"utterances":[],"text":"hello world supo clip rules."}',
        encoding="utf-8",
    )
    return video


# ---------------------------------------------------------------------------
# Recording runner: runs the REAL binaries, records every command
# ---------------------------------------------------------------------------
class RecordingRunner:
    """run_ffmpeg_command replacement that executes real ffmpeg/ffprobe.

    Bare "ffmpeg"/"ffprobe" tokens are rewritten to the resolved executables
    (they are not on PATH in every environment), the process really runs, and
    every (original command, result) pair is recorded.
    """

    def __init__(self, ffmpeg_bin: str, ffprobe_bin: str):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.calls: list = []

    def run(self, command, timeout=900):
        replaced = [
            self.ffmpeg_bin if arg == "ffmpeg"
            else self.ffprobe_bin if arg == "ffprobe"
            else arg
            for arg in command
        ]
        result = subprocess.run(
            replaced, capture_output=True, text=True, timeout=timeout
        )
        self.calls.append((list(command), result))
        return result


# ---------------------------------------------------------------------------
# Pure-Python frame parser (no PIL/numpy): rgb24 pipe -> non-white pixel count
# ---------------------------------------------------------------------------
def _count_non_white_pixels_in_band(
    ffmpeg_bin: str,
    video_path: Path,
    t: float,
    width: int,
    height: int,
    y_start: int,
    y_end: int,
) -> int:
    """Decode one frame at t and count pixels in rows [y_start, y_end) whose
    min(r,g,b) < 245 — i.e. anything that is NOT the white canvas."""
    result = subprocess.run(
        [
            ffmpeg_bin, "-v", "error",
            "-ss", f"{t:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, f"frame extraction failed:\n{result.stderr[-1500:]}"
    expected = width * height * 3
    assert len(result.stdout) >= expected, (
        f"short raw frame: got {len(result.stdout)} bytes, expected {expected}"
    )
    data = result.stdout[:expected]
    non_white = 0
    band_end = min(y_end, height)
    for y in range(y_start, band_end):
        row_offset = y * width * 3
        for x in range(width):
            i = row_offset + x * 3
            if min(data[i], data[i + 1], data[i + 2]) < 245:
                non_white += 1
    return non_white


# ---------------------------------------------------------------------------
# NEW contract proof: 1080x1920 white canvas; captions burned, control blank
# ---------------------------------------------------------------------------
# 640x360 (16:9) fit into 1080x1920: scale to 1080x608, banded 656/656.
# The bottom band of white canvas is y in [1264, 1920) — 656 rows.
CANVAS_W, CANVAS_H = 1080, 1920
BAND_START, BAND_END = 1264, 1920


class TestOriginalFormatWhiteCanvas:
    def test_captions_burned_on_1080x1920_white_canvas(
        self, monkeypatch, ffmpeg_toolchain, caption_clip, tmp_path
    ):
        out_a = tmp_path / "captions_on.mp4"
        out_b = tmp_path / "captions_off.mp4"

        runner = RecordingRunner(
            ffmpeg_toolchain["ffmpeg"], ffmpeg_toolchain["ffprobe"]
        )
        monkeypatch.setattr(video_utils, "run_ffmpeg_command", runner.run)

        ok_a = video_utils.create_optimized_clip(
            caption_clip,
            0.5,
            2.5,
            out_a,
            add_subtitles=True,
            caption_template="default",
            output_format="original",
            keep_ranges=[(0.5, 2.5)],
        )
        ok_b = video_utils.create_optimized_clip(
            caption_clip,
            0.5,
            2.5,
            out_b,
            add_subtitles=False,
            caption_template="default",
            output_format="original",
            keep_ranges=[(0.5, 2.5)],
        )

        assert ok_a is True, "create_optimized_clip(add_subtitles=True, original) returned False"
        assert ok_b is True, "create_optimized_clip(add_subtitles=False, original) returned False"
        assert out_a.exists() and out_a.stat().st_size > 0, "captions-on output missing/empty"
        assert out_b.exists() and out_b.stat().st_size > 0, "captions-off output missing/empty"

        size_a = video_utils.ffprobe_video_size(out_a)
        size_b = video_utils.ffprobe_video_size(out_b)
        assert size_a == (CANVAS_W, CANVAS_H), (
            f"original format (captions) size {size_a}, expected {CANVAS_W}x{CANVAS_H}"
        )
        assert size_b == (CANVAS_W, CANVAS_H), (
            f"original format (no captions) size {size_b}, expected {CANVAS_W}x{CANVAS_H}"
        )

        non_white_a = _count_non_white_pixels_in_band(
            ffmpeg_toolchain["ffmpeg"],
            out_a,
            1.0,
            CANVAS_W,
            CANVAS_H,
            BAND_START,
            BAND_END,
        )
        non_white_b = _count_non_white_pixels_in_band(
            ffmpeg_toolchain["ffmpeg"],
            out_b,
            1.0,
            CANVAS_W,
            CANVAS_H,
            BAND_START,
            BAND_END,
        )

        assert non_white_a > 50, (
            "captions NOT burned into the original white canvas: "
            f"only {non_white_a} non-white pixels in band y=[{BAND_START},{BAND_END}) "
            "at t=1.0s (expected > 50)"
        )
        assert non_white_b < 10, (
            "control render shows content/caption pixels where the white canvas "
            f"should be: {non_white_b} non-white pixels in band y=[{BAND_START},{BAND_END}) "
            "at t=1.0s (expected < 10)"
        )
