"""Falsification tests for the original-format WHITE-CANVAS layout contract.

New contract (feature pin — do not rename terms):

1. `compute_white_canvas_layout(src_w, src_h)` returns
   `(fit_w, fit_h, top_white, bottom_white)` for a 1080x1920 canvas: the source
   is fit full-frame (preserving aspect, even-truncated) and centred, leaving
   two white bands above and below.

2. `render_reframed_clip_ffmpeg(..., output_format="original")` builds a
   REAL re-encode command (never `-c copy`) whose video filter carries
   `force_original_aspect_ratio=decrease`, `pad=1080:1920:(ow-iw)/2:(oh-ih)/2`,
   `color=white` and `setsar=1`, and contains NO fade.

On the current code both contracts fail: `compute_white_canvas_layout` does not
exist (AttributeError) and the original render branch keeps the source size
without pad/white.
"""

from pathlib import Path

import pytest

from src import video_utils


def _is_stream_copy(command) -> bool:
    return any(
        token == "-c"
        and index + 1 < len(command)
        and command[index + 1] == "copy"
        for index, token in enumerate(command)
    )


class TestComputeWhiteCanvasLayout:
    def test_landscape_source_fits_1080x608_with_even_bands(self):
        fit_w, fit_h, top, bottom = video_utils.compute_white_canvas_layout(
            640, 360
        )
        assert (fit_w, fit_h) == (1080, 608), (
            f"640x360 must fit full-frame to (1080, 608), got {(fit_w, fit_h)}"
        )
        assert top == pytest.approx(656, abs=2), f"top white band {top}"
        assert bottom == pytest.approx(656, abs=2), f"bottom white band {bottom}"
        assert top + bottom == 1920 - fit_h, "bands must tile the leftover height"

    def test_square_source_fits_1080x1080_with_420_bands(self):
        fit_w, fit_h, top, bottom = video_utils.compute_white_canvas_layout(
            1080, 1080
        )
        assert (fit_w, fit_h) == (1080, 1080), (
            f"1080x1080 must fit full-frame to (1080, 1080), got {(fit_w, fit_h)}"
        )
        assert top == pytest.approx(420, abs=2), f"top white band {top}"
        assert bottom == pytest.approx(420, abs=2), f"bottom white band {bottom}"
        assert top + bottom == 1920 - fit_h, "bands must tile the leftover height"

    def test_portrait_source_fills_canvas_with_zero_bands(self):
        fit_w, fit_h, top, bottom = video_utils.compute_white_canvas_layout(
            1080, 1920
        )
        assert (fit_w, fit_h) == (1080, 1920), (
            f"1080x1920 must fill the canvas exactly, got {(fit_w, fit_h)}"
        )
        assert top == 0, f"top white band {top}"
        assert bottom == 0, f"bottom white band {bottom}"


class TestRenderReframedOriginalWhiteCanvas:
    """Integration: the original render command must pad white, never fade/copy."""

    def _patch(self, monkeypatch, *, captured: list):
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda p: (640, 360))
        monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda p: False)
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda p: 3.0)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(command, timeout=900):
            captured.append(command)
            return Result()

        monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run)

    def test_original_render_pads_white_1080x1920_and_never_fades_or_copies(
        self, monkeypatch, tmp_path
    ):
        captured: list[list[str]] = []
        self._patch(monkeypatch, captured=captured)

        ok, out_w, out_h = video_utils.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            "original",
            subtitle_ass_path=None,
        )

        assert ok is True
        assert (out_w, out_h) == (1080, 1920), (
            f"original render must report the white canvas size, got {(out_w, out_h)}"
        )
        assert captured, "original render must shell out to ffmpeg (no stream copy)"
        command = captured[0]
        vf_value = command[command.index("-vf") + 1]

        assert "force_original_aspect_ratio=decrease" in vf_value, (
            f"original filter must fit the source with force_original_aspect_ratio=decrease: {vf_value!r}"
        )
        assert "pad=1080:1920" in vf_value, (
            f"original filter must pad onto 1080x1920: {vf_value!r}"
        )
        assert "color=white" in vf_value, (
            f"original filter must pad with the WHITE colour: {vf_value!r}"
        )
        assert "setsar=1" in vf_value, (
            f"original filter must force SAR 1: {vf_value!r}"
        )
        assert "fade" not in " ".join(command), (
            f"original render must NOT fade: {command!r}"
        )
        assert not _is_stream_copy(command), (
            f"original render must NOT stream-copy: {command!r}"
        )
