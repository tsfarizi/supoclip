"""RED falsification tests for the burned-in @watermark in ASS rendering.

Feature pin (contract — do not rename terms):

1. `build_watermark_ass(...)` renders the watermark in the TOP BAND, BELOW the
   hook title: effective margin = hook margin + `WATERMARK_V_OFFSET` (90px).
2. Watermark text is auto-prefixed with `@`; a user-provided leading `@` or
   leading/trailing whitespace is normalized (never a double `@@`).
3. `watermark_persist=True` -> the watermark Dialogue event ENDS at the FULL
   `output_duration` (the watermark stays on screen the whole clip).
   `watermark_persist=False` -> the event ENDS at
   `min(HOOK_TITLE_SECONDS, max(HOOK_TITLE_MIN_SECONDS, output_duration - 0.25))`
   (mirror of the hook window).
4. `build_assemblyai_ass_subtitles` gains trailing keyword params
   `watermark: Optional[str] = None`, `watermark_persist: bool = False`,
   `watermark_margin_v_override: Optional[float] = None`, and renders the
   watermark even when `hook_title is None`.
5. `create_optimized_clip` gains `watermark` and `watermark_persist` (after
   `hook_persist`) and forwards them to `build_assemblyai_ass_subtitles`.

Every test is RED on the current code: `build_watermark_ass` does not exist
(AttributeError at the call site) and the two callers reject the new keyword
arguments (TypeError). `WATERMARK_V_OFFSET` does not exist either; the pinned
value 90 is used until the constant lands.
"""

from pathlib import Path

import pytest

from src import video_utils
from src.video_utils import (
    HOOK_TITLE_MIN_SECONDS,
    HOOK_TITLE_SECONDS,
    create_optimized_clip,
)

# `build_watermark_ass` is deliberately NOT imported at module scope: importing
# a missing name would abort collection with a single ImportError. Accessing it
# as `video_utils.build_watermark_ass` fails at call time so each clause gets
# its own RED report.
WATERMARK_V_OFFSET_PINNED = 90

DEFAULT_TEMPLATE = {
    "font_color": "#FFFFFF",
    "highlight_color": "#FFE000",
    "stroke_color": "#000000",
    "stroke_width": 3,
    "uppercase": False,
    "shadow": True,
    "word_pop": True,
    "background_color": None,
}


def _ass_ts_to_seconds(ts: str) -> float:
    hours, minutes, rest = ts.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _parse_dialogue(line: str) -> dict:
    """Parse an ASS Dialogue line into its format fields.

    The Text field may contain commas (e.g. \\fad(160,240), \\t(0,160,...)),
    so the split is limited to the 9 structural commas.
    """
    parts = line.split(",", 9)
    return {
        "layer": parts[0].split(":", 1)[1].strip(),
        "start": parts[1],
        "end": parts[2],
        "style": parts[3],
        "name": parts[4],
        "margin_l": parts[5],
        "margin_r": parts[6],
        "margin_v": parts[7],
        "effect": parts[8],
        "text": parts[9] if len(parts) > 9 else "",
    }


def _dialogue_events_from_ass(ass_path: Path) -> list[dict]:
    """All `Dialogue:` events from a written ASS file."""
    events = []
    for raw in ass_path.read_text(encoding="utf-8").splitlines():
        if not raw.startswith("Dialogue:"):
            continue
        events.append(_parse_dialogue(raw))
    return events


class TestBuildWatermarkAss:
    def _build(self, **kwargs):
        params = dict(
            watermark="myhandle",
            template=DEFAULT_TEMPLATE,
            video_width=1080,
            video_height=1920,
            output_duration=12.0,
            font_name="Arial",
            caption_font_px=62,
            hook_margin_v=200,
        )
        params.update(kwargs)
        style_line, events = video_utils.build_watermark_ass(**params)
        assert len(events) == 1, f"expected exactly one watermark event, got {events!r}"
        return _parse_dialogue(events[0]), style_line

    def test_build_watermark_ass_prefixes_at(self):
        event, _ = self._build(watermark="myhandle")
        assert "@myhandle" in event["text"], (
            f"watermark text must be auto-prefixed with '@', got {event['text']!r}"
        )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("@myhandle", "@myhandle"),
            ("  @handle ", "@handle"),
        ],
    )
    def test_build_watermark_ass_normalizes_leading_at(self, raw, expected):
        event, _ = self._build(watermark=raw)
        assert expected in event["text"], (
            f"watermark {raw!r} must normalize to {expected!r}, got {event['text']!r}"
        )
        assert "@@" not in event["text"], (
            f"normalization must never produce a double '@@', got {event['text']!r}"
        )
        assert not event["text"].lstrip().startswith("@@" + expected[1:]), (
            f"leading '@' must not be duplicated, got {event['text']!r}"
        )

    def test_build_watermark_ass_below_hook(self):
        hook_margin_v = 200
        offset = getattr(video_utils, "WATERMARK_V_OFFSET", WATERMARK_V_OFFSET_PINNED)
        event, _ = self._build(watermark="myhandle", hook_margin_v=hook_margin_v)
        expected_margin = hook_margin_v + offset
        assert int(event["margin_v"]) == expected_margin, (
            f"watermark must sit BELOW the hook: margin = hook_margin_v + "
            f"WATERMARK_V_OFFSET = {hook_margin_v} + {offset} = {expected_margin}, "
            f"got event MarginV {event['margin_v']!r}"
        )
        assert int(event["margin_v"]) > hook_margin_v, (
            f"watermark margin must be strictly greater than the hook margin, "
            f"got {event['margin_v']!r} <= {hook_margin_v}"
        )

    def test_build_watermark_ass_persist_ends_at_output_duration(self):
        event, _ = self._build(watermark="myhandle", watermark_persist=True, output_duration=12.0)
        assert _ass_ts_to_seconds(event["end"]) == 12.0, (
            f"persisted watermark must end at the FULL output_duration, got {event['end']!r}"
        )

    def test_build_watermark_ass_default_ends_at_window(self):
        event, _ = self._build(watermark="myhandle", watermark_persist=False, output_duration=12.0)
        expected = min(
            HOOK_TITLE_SECONDS,
            max(HOOK_TITLE_MIN_SECONDS, 12.0 - 0.25),
        )
        assert _ass_ts_to_seconds(event["end"]) == expected, (
            f"non-persisted watermark must end at "
            f"min(HOOK_TITLE_SECONDS, max(HOOK_TITLE_MIN_SECONDS, output_duration - 0.25))"
            f" = {expected}, got {event['end']!r}"
        )


class TestBuildAssemblyAiAssSubtitlesWatermark:
    def test_build_assemblyai_ass_subtitles_accepts_watermark(self, tmp_path):
        video_path = tmp_path / "no_sidecar.mp4"
        ass_path = tmp_path / "captions.ass"
        ok = video_utils.build_assemblyai_ass_subtitles(
            video_path=video_path,
            clip_start=0.5,
            clip_end=12.5,
            video_width=1080,
            video_height=1920,
            output_ass_path=ass_path,
            hook_title=None,
            watermark="handle",
        )
        assert ok is True, "watermark-only ASS build returned False"
        assert ass_path.exists()
        events = _dialogue_events_from_ass(ass_path)
        assert any("@handle" in e["text"] for e in events), (
            f"ASS must contain a watermark event with '@handle', got {events!r}"
        )


class TestCreateOptimizedClipForwardsWatermark:
    def test_create_optimized_clip_forwards_watermark(self, monkeypatch, tmp_path):
        captured_kwargs: dict = {}

        class _CompletedProcess:
            returncode = 0
            stderr = ""

        def fake_render_source_ranges_ffmpeg(_video_path, _keep_ranges, output_path):
            Path(output_path).write_bytes(b"video")
            return True

        def fake_build_assemblyai_ass_subtitles(*args, **kwargs):
            captured_kwargs.update(kwargs)
            Path(args[5]).write_text(
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n",
                encoding="utf-8",
            )
            return True

        def fake_run_ffmpeg_command(command, **_kwargs):
            Path(command[-1]).write_bytes(b"video")
            return _CompletedProcess()

        monkeypatch.setattr(
            video_utils,
            "render_source_ranges_ffmpeg",
            fake_render_source_ranges_ffmpeg,
        )
        monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda _path: (640, 360))
        monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: False)
        monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _path: 10.0)
        monkeypatch.setattr(
            video_utils,
            "build_assemblyai_ass_subtitles",
            fake_build_assemblyai_ass_subtitles,
        )
        monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run_ffmpeg_command)
        # Dotted-path target: `video_utils.shutil` is the stdlib module bound as
        # an attribute of video_utils, so the string form is required for the
        # attribute traversal.
        monkeypatch.setattr("src.video_utils.shutil.move", lambda _src, _dst: None)

        ok = create_optimized_clip(
            video_path=tmp_path / "input.mp4",
            start_time=0.5,
            end_time=12.0,
            output_path=tmp_path / "clip.mp4",
            keep_ranges=[(0.5, 12.0)],
            watermark="handle",
            watermark_persist=True,
        )

        assert ok is True, "create_optimized_clip returned False"
        assert captured_kwargs.get("watermark") == "handle", (
            f"watermark must be forwarded to build_assemblyai_ass_subtitles, "
            f"got kwargs {captured_kwargs!r}"
        )
        assert captured_kwargs.get("watermark_persist") is True, (
            f"watermark_persist must be forwarded to build_assemblyai_ass_subtitles, "
            f"got kwargs {captured_kwargs!r}"
        )
