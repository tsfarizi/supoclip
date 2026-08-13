"""
Golden end-to-end harness for horizontal-content reframing.

Falsifies two contracts through the REAL ffmpeg binary (the render pass is
never mocked):

1. OPSI 2 (smart-fit in 9:16): a clip whose scenes are horizontal content
   (slides/screen recordings — no detectable face) must render on the default
   "vertical" path with the content-slot filter scaled-to-FILL
   (`[ftsrc]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920`)
   instead of the historical letterboxed `force_original_aspect_ratio=decrease`
   strip, and must keep the end-of-clip fade.

2. OPSI 1 (auto-aspect): the same content-dominated clip rendered with
   output_format="auto" must resolve to LANDSCAPE (1920x1080 full frame), not
   a 9:16 vertical crop, and keep the fade.

Fixture: a 5s 1920x1080 "slide deck" made of plain geometric boxes — chosen so
the OpenCV Haar cascade does NOT report a face (a face sprite would switch the
scene to the tracked-crop path and void this contract). Detection is
environment-dependent; the assertions report the recorded commands and logged
warnings honestly instead of masking a miss.

ffmpeg resolution (AS-1): the toolchain is resolved via Config().ffmpeg_bin_dir
then PATH; when absent the suite is SKIPPED, never failed.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src import video_utils

# Smart-fit content slot: scale-to-fill + centered crop on the 1080x1920 frame.
FIT_FILL_RE = re.compile(
    r"\[ftsrc\]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
    r"crop=1080:1920:x='trunc\(\(iw-1080\)\*[0-9.]+\/2\)\*2'"
)
FIT_LETTERBOX_RE = re.compile(
    r"\[ftsrc\]scale=1080:1920:force_original_aspect_ratio=decrease"
)


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


def _run(cmd, timeout=300):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, f"fixture generation failed:\n{result.stderr[-1500:]}"
    return result


@pytest.fixture(scope="session")
def slide_deck_video(tmp_path_factory, ffmpeg_toolchain):
    """A 5s 1920x1080 'slide deck' of geometric boxes, no face-like shape."""
    work_dir = tmp_path_factory.mktemp("golden_content")
    ffmpeg_bin = ffmpeg_toolchain["ffmpeg"]
    video = work_dir / "slide_deck.mp4"

    # Three boxy "slides" over time. Plain rectangles; nothing resembling a
    # face so the fit/content path is exercised, not the tracked-face path.
    filter_complex = (
        "[0:v]drawbox=x=100:y=100:w=1500:h=600:color=0x2d6cdf:t=fill,"
        "drawbox=x=100:y=750:w=1000:h=150:color=0x222222:t=fill,"
        "drawbox=x=300:y=100:w=1200:h=600:color=0x1a9e6c:t=fill,"
        "drawbox=x=1200:y=750:w=400:h=150:color=0xb03a2e:t=fill[v]"
    )
    _run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=5:r=30",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=5",
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "5",
            str(video),
        ],
        timeout=300,
    )
    assert video.exists() and video.stat().st_size > 0
    return video


class RecordingRunner:
    """run_ffmpeg_command replacement that executes real ffmpeg/ffprobe."""

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


def _render(monkeypatch, toolchain, input_path, output_path, output_format):
    runner = RecordingRunner(toolchain["ffmpeg"], toolchain["ffprobe"])
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", runner.run)
    ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
        input_path, output_path, output_format
    )
    render_command = None
    for command, _result in runner.calls:
        if "-vf" in command or "-filter_complex" in command:
            render_command = command
    return ok, out_w, out_h, render_command, runner.calls


def _vf_of(command) -> str:
    if command is None:
        return ""
    if "-vf" in command:
        return command[command.index("-vf") + 1]
    if "-filter_complex" in command:
        return command[command.index("-filter_complex") + 1]
    return ""


def _logged_warnings(caplog):
    return [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]


class TestVerticalContentFit:
    def test_content_clip_renders_9x16_with_fill_chain(
        self, monkeypatch, ffmpeg_toolchain, slide_deck_video, tmp_path, caplog
    ):
        """OPSI 2: default vertical renders the horizontal content FILLED."""
        output_path = tmp_path / "slide_vertical.mp4"
        ok, out_w, out_h, render_command, _ = _render(
            monkeypatch, ffmpeg_toolchain, slide_deck_video, output_path, "vertical"
        )

        assert ok is True, "render_reframed_clip_ffmpeg('vertical') returned False"
        assert output_path.exists() and output_path.stat().st_size > 0
        assert (out_w, out_h) == (1080, 1920)
        vf = _vf_of(render_command)
        if FIT_FILL_RE.search(vf) is None:
            pytest.fail(
                "OPSI 2 FAILED: content clip did not use the fill chain "
                "(force_original_aspect_ratio=increase + crop).\n"
                f"vf={vf[:600]!r}\n"
                f"letterbox present: {bool(FIT_LETTERBOX_RE.search(vf))}\n"
                f"logged warnings: {_logged_warnings(caplog)[:6]}"
            )
        assert ",fade=t=out:st=" in vf or "[vout]fade=t=out:st=" in vf, (
            f"fade missing from fill chain: {vf[:400]}"
        )

    def test_content_clip_auto_resolves_to_landscape_16x9(
        self, monkeypatch, ffmpeg_toolchain, slide_deck_video, tmp_path, caplog
    ):
        """OPSI 1: content-dominated clip under 'auto' renders full 16:9."""
        output_path = tmp_path / "slide_auto.mp4"
        ok, out_w, out_h, render_command, _ = _render(
            monkeypatch, ffmpeg_toolchain, slide_deck_video, output_path, "auto"
        )

        assert ok is True, "render_reframed_clip_ffmpeg('auto') returned False"
        assert output_path.exists() and output_path.stat().st_size > 0
        if (out_w, out_h) != (1920, 1080):
            pytest.fail(
                "OPSI 1 FAILED: content-dominated clip did not resolve to "
                "landscape 16:9 under 'auto'.\n"
                f"resolved size={(out_w, out_h)}\n"
                f"logged warnings: {_logged_warnings(caplog)[:6]}"
            )
        vf = _vf_of(render_command)
        assert "scale=1920:1080" in vf, f"landscape scale missing: {vf[:400]}"
        assert ",fade=t=out:st=" in vf, f"fade missing from landscape chain: {vf[:400]}"
