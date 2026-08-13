"""Golden end-to-end harness: original-format clip with burned word captions.

Verifies the F1 fix's runtime contract through the REAL ffmpeg binary — the
render pass is never mocked. The only test-side wrapper around
`video_utils.run_ffmpeg_command` substitutes the resolved ffmpeg/ffprobe
executables for the bare names (matching `test_clip_quality.py`).

Contracts under falsification
-----------------------------
1. `create_optimized_clip(..., output_format="original", add_subtitles=True)`
   burns word-synced captions INTO the output frame: with a valid transcript
   sidecar (<video>.transcript_cache.json) present, a frame extracted at
   t=1.0s must contain bright caption pixels (white text on black). This is
   the exact payload the F1 guard must guarantee: the sidecar must exist
   before an "original" clip with captions can render anything but a bare
   hook title.
2. Control — `add_subtitles=False` must render NO caption pixels: the
   original-format fast path (stream copy, F4) keeps the source frame
   untouched, so the bright-pixel count stays at ~0.

Honesty notes (read before trusting a red/green)
------------------------------------------------
* The ASS burn happens inside `render_reframed_clip_ffmpeg` via the
  `subtitles` filter (libass). If the bundled ffmpeg lacks libass the render
  returns False and the test FAILS honestly — it is never masked.
* The stream-copy control path shells to bare `ffmpeg` directly; the
  session-autouse `ffmpeg_bin_dir_on_path` fixture in tests/conftest.py puts
  the bundled binaries on PATH.
* The synthetic clip is solid black + a sine tone: every bright pixel in the
  A render is caption text, there is no scene content to confuse the count.
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
# Pure-Python frame parser (no PIL/numpy): rgb24 pipe -> bright pixel count
# ---------------------------------------------------------------------------
def _count_bright_pixels(ffmpeg_bin: str, video_path: Path, t: float, width: int, height: int) -> int:
    """Decode one frame at t and count pixels with max(r,g,b) > 200."""
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
    bright = 0
    for i in range(0, expected, 3):
        if max(data[i], data[i + 1], data[i + 2]) > 200:
            bright += 1
    return bright


# ---------------------------------------------------------------------------
# F1 runtime proof: original format + burned captions vs stream-copy control
# ---------------------------------------------------------------------------
class TestOriginalFormatCaptions:
    def test_captions_burned_into_original_format(
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

        width, height = video_utils.ffprobe_video_size(out_a)
        assert (width, height) == (640, 360), f"original format size {(width, height)}"

        bright_a = _count_bright_pixels(
            ffmpeg_toolchain["ffmpeg"], out_a, 1.0, width, height
        )
        bright_b = _count_bright_pixels(
            ffmpeg_toolchain["ffmpeg"], out_b, 1.0, width, height
        )

        assert bright_a > 50, (
            "captions NOT burned into original format: "
            f"only {bright_a} bright pixels at t=1.0s (expected > 50)"
        )
        assert bright_b < 10, (
            "control render shows unexpected caption pixels: "
            f"{bright_b} bright pixels at t=1.0s (expected < 10)"
        )
