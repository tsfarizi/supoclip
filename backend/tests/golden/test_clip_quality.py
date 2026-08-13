"""
Golden end-to-end harness for SupoClip vertical clip framing & fade-out.

Verifies the F2 (auto speaker-pan) and E4 (end-of-clip fade-out) contracts
through the REAL ffmpeg binary — the render pass is never mocked. The only
test-side wrapper around `video_utils.run_ffmpeg_command` substitutes the
resolved ffmpeg/ffprobe executables for the bare names and records every
command so the assertions can inspect what was actually executed.

Contracts under falsification
-----------------------------
1. `render_reframed_clip_ffmpeg(input, output, "vertical")` renders a vertical
   1080x1920 clip on the default "vertical" path (render success + output file
   + ffprobe-reported geometry).
2. E4 — EVERY vertical branch appends the end-of-clip fade: the video chain
   ends with `fade=t=out:st=...:d=...` and, when the clip has audio, the
   command carries `-af afade=t=out:...`.
3. F2 — on the default "vertical" path, a clip with TWO detectable face
   regions and <= 2 scene cuts must select the speaker-pan crop
   (`crop=W:H:x='<expr>':y=0`); a single-face clip must NOT.

Honesty notes (read before trusting a red/green)
------------------------------------------------
* Synthetic vs real faces. The fixtures are drawn cartoon "faces" (skin box +
  eyes + mouth) that this environment's OpenCV Haar cascade happens to
  detect. Detection is environment-dependent: with a different OpenCV build or
  with MediaPipe installed the sprites may or may not be detected. When the
  detector finds nothing, `detect_speaker_reframe_plan` returns None and the
  pan assertion FAILS — the failure message then reports the recorded plan
  status and the logged warnings. It is never masked as a pass.
* The naive assertion "the command contains `x='`" is AMBIGUOUS: the Ken
  Burns punch-in for static all-face clips emits its own `x='(iw-iw/zoom)/2'`
  inside the zoompan filter. The pan-specific pattern asserted here is
  `crop=<w>:<h>:x='` (the speaker-pan crop chain), which zoompan never
  produces.
* Out of scope: E1 (sentence-end extension), E2 (silence-gap trim) and E3
  (scene-cut snap) all require cached transcript data
  (`load_cached_transcript_data`), which synthetic clips never have. Those
  extensions are therefore NOT triggered by this harness; they are covered by
  `tests/unit/test_video_utils_*.py`.
* ffmpeg resolution (AS-1): the toolchain is resolved via
  `Config().ffmpeg_bin_dir` (env `FFMPEG_BIN_DIR`, legacy default) and then
  falls back to `shutil.which`. If neither yields an executable, the golden
  tests are SKIPPED with an explicit reason — never failed.

Fixtures (session-scoped, generated ONCE with the real ffmpeg)
---------------------------------------------------------------
* two_speakers.mp4 — two face sprites on black; the LEFT sprite moves during
  the first half of the clip, the RIGHT during the second half, so the
  speaker-motion pass derives a two-segment timeline (left -> right).
* single_face.mp4  — one centre sprite drifting very slowly (sub-deadzone
  amplitude), so the crop stays static on every environment.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src import video_utils

# F2's speaker-pan crop chain is `crop=<w>:<h>:x='<expr>'`. The Ken Burns
# zoompan fragment also emits `x='(iw-iw/zoom)/2'`, so a bare `x='` search is
# not a valid falsification of the speaker-pan selection.
PAN_CROP_RE = re.compile(r"crop=\d+:\d+:x='")


# ---------------------------------------------------------------------------
# ffmpeg resolution (AS-1: skip, never fail, when the toolchain is missing)
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
# Synthetic fixture generation (session-scoped, real ffmpeg, once)
# ---------------------------------------------------------------------------
def _run(cmd, timeout=300):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, f"fixture generation failed:\n{result.stderr[-1500:]}"
    return result


def _generate_face_sprite(work_dir: Path, ffmpeg_bin: str) -> Path:
    """Cartoon face sprite (300x400) that the Haar cascade can detect."""
    sprite = work_dir / "face.png"
    vf = (
        "drawbox=x=0:y=0:w=300:h=400:color=0xE8B98E:t=fill,"
        "drawbox=x=60:y=60:w=55:h=70:color=0x1a1a1a:t=fill,"
        "drawbox=x=185:y=60:w=55:h=70:color=0x1a1a1a:t=fill,"
        "drawbox=x=80:y=210:w=140:h=45:color=0x40261a:t=fill"
    )
    _run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=300x400:d=1:r=1",
            "-vf", vf,
            "-frames:v", "1",
            str(sprite),
        ]
    )
    assert sprite.exists() and sprite.stat().st_size > 0
    return sprite


@pytest.fixture(scope="session")
def synthetic_videos(tmp_path_factory, ffmpeg_toolchain):
    """Generate the two 5s/30fps 1280x720 horizontal clips once per session."""
    work_dir = tmp_path_factory.mktemp("golden_videos")
    ffmpeg_bin = ffmpeg_toolchain["ffmpeg"]
    sprite = _generate_face_sprite(work_dir, ffmpeg_bin)

    two = work_dir / "two_speakers.mp4"
    one = work_dir / "single_face.mp4"

    # two_speakers: left sprite moves 0..2.5s, right sprite moves 2.5..5s, so
    # the motion pass yields a left -> right speaker timeline.
    left_x = "90+70*sin(2*PI*t)*if(lt(t\\,2.5)\\,1\\,0)"
    right_x = "920+70*sin(2*PI*t)*if(gte(t\\,2.5)\\,1\\,0)"
    filter_complex = (
        f"[0:v][2:v]overlay=x='{left_x}':y=260[t1];"
        f"[t1][2:v]overlay=x='{right_x}':y=260[v]"
    )
    _run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=5:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
            "-loop", "1", "-i", str(sprite),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "5",
            str(two),
        ],
        timeout=300,
    )

    # single_face: one centre sprite drifting slowly. The amplitude (8px) is
    # below the tracked-crop deadzone (~20px for a 404px crop), so the crop
    # stays STATIC on every environment — the only x-expression that may
    # appear is the Ken Burns zoompan's own `x='(iw-iw/zoom)/2'`.
    centre_x = "646+8*sin(2*PI*t/5)"
    _run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=5:r=30",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=5",
            "-loop", "1", "-i", str(sprite),
            "-filter_complex", f"[0:v][2:v]overlay=x='{centre_x}':y=220[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "5",
            str(one),
        ],
        timeout=300,
    )

    assert two.exists() and two.stat().st_size > 0
    assert one.exists() and one.stat().st_size > 0
    return {"two_speakers": two, "single_face": one}


# ---------------------------------------------------------------------------
# Recording runner: runs the REAL binaries, records every command
# ---------------------------------------------------------------------------
class RecordingRunner:
    """run_ffmpeg_command replacement that executes real ffmpeg/ffprobe.

    Bare "ffmpeg"/"ffprobe" tokens are rewritten to the resolved executables
    (they are not on PATH in every environment), the process really runs, and
    every (original command, result) pair is recorded for assertions.
    """

    def __init__(self, ffmpeg_bin: str, ffprobe_bin: str):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.calls: list = []  # (command: list[str], result: CompletedProcess)

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


def _render_with_recording(monkeypatch, toolchain, input_path, output_path):
    """Patch run_ffmpeg_command with the recording runner, render "vertical".

    Returns (ok, out_w, out_h, render_command, runner.calls). The render
    command is the last recorded command that carries a video filter
    (-vf/-filter_complex), i.e. the final reframing pass.
    """
    runner = RecordingRunner(toolchain["ffmpeg"], toolchain["ffprobe"])
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", runner.run)

    ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
        input_path, output_path, "vertical"
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


def _plan_status(plan_results) -> str:
    if not plan_results:
        return "detect_speaker_reframe_plan was NOT invoked"
    return "; ".join(
        f"{entry['format']} -> mode={entry['plan'].get('mode') if entry['plan'] else None}"
        for entry in plan_results
    )


def _logged_warnings(caplog):
    return [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]


def _assert_vertical_render_contract(ok, out_w, out_h, output_path, render_command):
    """Contract clause: the vertical render succeeds with a 1080x1920 file."""
    assert ok is True, "render_reframed_clip_ffmpeg('vertical') returned False"
    assert output_path.exists(), f"output file missing: {output_path}"
    assert output_path.stat().st_size > 0, f"output file empty: {output_path}"
    assert (out_w, out_h) == (1080, 1920), f"render returned size {(out_w, out_h)}"
    assert video_utils.ffprobe_video_size(output_path) == (
        1080,
        1920,
    ), "ffprobe-reported output size is not 1080x1920"
    assert render_command is not None, "no ffmpeg render command was recorded"


def _assert_fade_out_in_command(render_command):
    """Contract clause (E4): video fade appended last + audio afade when audio."""
    assert render_command is not None, "no ffmpeg render command was recorded"
    vf = _vf_of(render_command)
    # The video fade must be the LAST element of the filter chain.
    assert ",fade=t=out:st=" in vf, f"video fade missing from vf chain: {vf[:400]}"
    assert vf.rsplit(",", 1)[-1].startswith(
        "fade=t=out:st="
    ), f"video fade is not appended last: {vf[:400]}"
    joined = " ".join(render_command)
    assert "-af" in render_command, "audio flag -af missing from render command"
    assert "afade=t=out:st=" in joined, "audio afade missing from render command"


# ---------------------------------------------------------------------------
# two_speakers.mp4 — F2 speaker-pan expected on the default vertical path
# ---------------------------------------------------------------------------
class TestTwoSpeakersVertical:
    def test_render_contract_frames_and_fades(
        self, monkeypatch, ffmpeg_toolchain, synthetic_videos, tmp_path
    ):
        output_path = tmp_path / "two_speakers_vertical.mp4"
        ok, out_w, out_h, render_command, _ = _render_with_recording(
            monkeypatch, ffmpeg_toolchain, synthetic_videos["two_speakers"], output_path
        )
        _assert_vertical_render_contract(ok, out_w, out_h, output_path, render_command)
        _assert_fade_out_in_command(render_command)

    def test_default_vertical_selects_speaker_pan(
        self, monkeypatch, ffmpeg_toolchain, synthetic_videos, tmp_path, caplog
    ):
        """F2: two faces + <=2 scene cuts -> speaker-pan crop on default mode."""
        output_path = tmp_path / "two_speakers_vertical.mp4"
        plan_results = []

        original_plan = video_utils.detect_speaker_reframe_plan

        def recording_plan(clip_path, output_format, face_centers=None, scene_cut_count=None):
            plan = original_plan(
                clip_path, output_format, face_centers=face_centers,
                scene_cut_count=scene_cut_count,
            )
            plan_results.append({"format": output_format, "plan": plan})
            return plan

        monkeypatch.setattr(video_utils, "detect_speaker_reframe_plan", recording_plan)

        ok, out_w, out_h, render_command, _ = _render_with_recording(
            monkeypatch, ffmpeg_toolchain, synthetic_videos["two_speakers"], output_path
        )
        _assert_vertical_render_contract(ok, out_w, out_h, output_path, render_command)

        vf = _vf_of(render_command)
        if PAN_CROP_RE.search(vf) is None:
            # Honest diagnosis, never masked: report whether the pan plan was
            # None (e.g. face detection found nothing, motion pass failed) and
            # surface the logged warnings.
            pytest.fail(
                "F2 gate FAILED end-to-end: the two-speaker clip did not get the "
                "speaker-pan crop on the default vertical path.\n"
                f"vf={vf[:400]!r}\n"
                f"plan status: {_plan_status(plan_results)}\n"
                f"logged warnings: {_logged_warnings(caplog)[:6]}"
            )


# ---------------------------------------------------------------------------
# single_face.mp4 — F2 must NOT select the speaker pan
# ---------------------------------------------------------------------------
class TestSingleFaceVertical:
    def test_render_contract_frames_and_fades(
        self, monkeypatch, ffmpeg_toolchain, synthetic_videos, tmp_path
    ):
        output_path = tmp_path / "single_face_vertical.mp4"
        ok, out_w, out_h, render_command, _ = _render_with_recording(
            monkeypatch, ffmpeg_toolchain, synthetic_videos["single_face"], output_path
        )
        _assert_vertical_render_contract(ok, out_w, out_h, output_path, render_command)
        _assert_fade_out_in_command(render_command)

    def test_default_vertical_does_not_select_speaker_pan(
        self, monkeypatch, ffmpeg_toolchain, synthetic_videos, tmp_path, caplog
    ):
        """F2 negative: a single-face clip must take the tracked/static path."""
        output_path = tmp_path / "single_face_vertical.mp4"
        plan_results = []

        original_plan = video_utils.detect_speaker_reframe_plan

        def recording_plan(clip_path, output_format, face_centers=None, scene_cut_count=None):
            plan = original_plan(
                clip_path, output_format, face_centers=face_centers,
                scene_cut_count=scene_cut_count,
            )
            plan_results.append({"format": output_format, "plan": plan})
            return plan

        monkeypatch.setattr(video_utils, "detect_speaker_reframe_plan", recording_plan)

        ok, out_w, out_h, render_command, _ = _render_with_recording(
            monkeypatch, ffmpeg_toolchain, synthetic_videos["single_face"], output_path
        )
        _assert_vertical_render_contract(ok, out_w, out_h, output_path, render_command)

        vf = _vf_of(render_command)
        match = PAN_CROP_RE.search(vf)
        if match is not None:
            pytest.fail(
                "F2 gate VIOLATED end-to-end: the single-face clip received the "
                "speaker-pan crop.\n"
                f"vf={vf[:400]!r}\n"
                f"plan status: {_plan_status(plan_results)}\n"
                f"logged warnings: {_logged_warnings(caplog)[:6]}"
            )


# ---------------------------------------------------------------------------
# create_optimized_clip — the full keep-ranges -> render pipeline
# ---------------------------------------------------------------------------
class TestCreateOptimizedClipPipeline:
    def test_full_pipeline_frames_and_fades(
        self, monkeypatch, ffmpeg_toolchain, synthetic_videos, tmp_path
    ):
        """create_optimized_clip: keep-ranges -> source render -> reframe + fade."""
        output_path = tmp_path / "pipeline_two_speakers.mp4"
        runner = RecordingRunner(ffmpeg_toolchain["ffmpeg"], ffmpeg_toolchain["ffprobe"])
        monkeypatch.setattr(video_utils, "run_ffmpeg_command", runner.run)

        ok = video_utils.create_optimized_clip(
            synthetic_videos["two_speakers"],
            0.0,
            5.0,
            output_path,
        )

        assert ok is True, "create_optimized_clip returned False"
        assert output_path.exists(), f"output file missing: {output_path}"
        assert output_path.stat().st_size > 0, f"output file empty: {output_path}"
        assert video_utils.ffprobe_video_size(output_path) == (
            1080,
            1920,
        ), "ffprobe-reported pipeline output size is not 1080x1920"

        # The reframe pass is the last command carrying a video filter.
        render_command = None
        for command, _result in runner.calls:
            if "-vf" in command or "-filter_complex" in command:
                render_command = command
        _assert_fade_out_in_command(render_command)
