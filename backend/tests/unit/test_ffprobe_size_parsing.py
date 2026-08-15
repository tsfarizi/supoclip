"""RED falsification tests: ffprobe size parsers must read the LAST non-empty line.

Real-world reproduction: `ffprobe -select_streams v:0 -show_entries stream=width,height
-of csv=s=x:p=0` on a .mov with two video streams (alternate group) emits MULTIPLE lines,
e.g. stdout == '1920x1080\n\n1920x1080\n'. Every parser below currently does
`stdout.strip().split("x", 1)` on the whole multi-line payload, so the second element is
`'1080\n\n1920x1080'` and `int(...)` raises ValueError (or the exception is swallowed and
a sentinel tuple is returned). Contract under test: the parser returns (width, height)
parsed from the last non-empty stream line, regardless of leading duplicate entries.

Tests 1-4 are RED on the current implementation. Test 5 is a control that must stay green.
"""

import subprocess
from pathlib import Path

from src import clip_editor, video_cache, video_utils, youtube_utils


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=0, stdout=stdout, stderr=""
    )


MULTI_LINE = "1920x1080\n\n1920x1080\n"


def test_video_utils_ffprobe_size_parses_last_line(monkeypatch):
    # Clause: ffprobe_video_size returns (width, height) from the last non-empty
    # stream line when ffprobe reports multiple video streams.
    monkeypatch.setattr(
        video_utils, "run_ffmpeg_command", lambda *args, **kwargs: _completed(MULTI_LINE)
    )
    assert video_utils.ffprobe_video_size(Path("x.mp4")) == (1920, 1080)


def test_clip_editor_ffprobe_size_parses_last_line(monkeypatch):
    # Clause: clip_editor._ffprobe_size returns (width, height) for multi-stream files.
    monkeypatch.setattr(
        clip_editor.subprocess, "run", lambda *args, **kwargs: _completed(MULTI_LINE)
    )
    assert clip_editor._ffprobe_size(Path("x.mp4")) == (1920, 1080)


def test_video_cache_probe_size_parses_last_line(monkeypatch):
    # Clause: video_cache._probe_size returns (width, height) for multi-stream files
    # instead of degrading to (None, None).
    monkeypatch.setattr(
        video_cache.subprocess, "run", lambda *args, **kwargs: _completed(MULTI_LINE)
    )
    assert video_cache._probe_size(Path("x.mp4")) == (1920, 1080)


def test_youtube_utils_probe_size_parses_last_line(monkeypatch):
    # Clause: youtube_utils._get_local_video_dimensions returns (width, height) for
    # multi-stream files instead of degrading to (0, 0).
    monkeypatch.setattr(
        youtube_utils.subprocess, "run", lambda *args, **kwargs: _completed(MULTI_LINE)
    )
    assert youtube_utils._get_local_video_dimensions(Path("x.mp4")) == (1920, 1080)


def test_video_utils_ffprobe_size_single_line(monkeypatch):
    # Control: single-line output keeps working; this must remain green.
    monkeypatch.setattr(
        video_utils, "run_ffmpeg_command", lambda *args, **kwargs: _completed("1920x1080\n")
    )
    assert video_utils.ffprobe_video_size(Path("x.mp4")) == (1920, 1080)
