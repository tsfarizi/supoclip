"""Falsification tests for hook-title persistence and margin override.

New contract (feature pin — do not rename terms):

1. `build_hook_title_ass` gains keyword params at the end:
   `hook_persist: bool = False` and `hook_margin_v_override: Optional[float] = None`.
2. When `hook_persist=True`, the hook Dialogue event ENDS at the FULL
   `output_duration` (the title stays on screen the whole clip) instead of
   `min(HOOK_TITLE_SECONDS, ...)`.
3. `hook_margin_v_override` replaces the event's MarginV field (currently 0).
4. `build_assemblyai_ass_subtitles` gains the same two keyword params and
   forwards them to `build_hook_title_ass`.

On the current code all of these fail with TypeError (the params do not exist).
"""

from pathlib import Path

from src import video_utils
from src.video_utils import HOOK_TITLE_SECONDS, build_hook_title_ass

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


def _hook_events_from_ass(ass_path: Path) -> list[dict]:
    """All `Dialogue:` events styled `Hook` from a written ASS file."""
    events = []
    for raw in ass_path.read_text(encoding="utf-8").splitlines():
        if not raw.startswith("Dialogue:"):
            continue
        fields = _parse_dialogue(raw)
        if fields["style"] == "Hook":
            events.append(fields)
    return events


class TestBuildHookTitleAssPersist:
    def _build(self, **kwargs):
        params = dict(
            hook_title="SHOCKING HEADLINE",
            template=DEFAULT_TEMPLATE,
            video_width=1080,
            video_height=1920,
            output_duration=12.0,
            font_name="Arial",
            caption_font_px=62,
        )
        params.update(kwargs)
        style_line, events = build_hook_title_ass(**params)
        assert len(events) == 1, f"expected exactly one hook event, got {events!r}"
        return _parse_dialogue(events[0]), style_line

    def test_hook_persist_true_ends_at_output_duration(self):
        event, _ = self._build(hook_persist=True, output_duration=12.0)
        assert event["style"] == "Hook"
        assert _ass_ts_to_seconds(event["end"]) == 12.0, (
            f"persisted hook must end at the FULL output_duration, got {event['end']!r}"
        )

    def test_hook_default_ends_at_min_hook_title_seconds(self):
        event, _ = self._build(hook_persist=False, output_duration=12.0)
        assert event["style"] == "Hook"
        expected = min(HOOK_TITLE_SECONDS, 12.0)
        assert _ass_ts_to_seconds(event["end"]) == expected, (
            f"non-persisted hook must end at min(HOOK_TITLE_SECONDS, output_duration)"
            f" = {expected}, got {event['end']!r}"
        )

    def test_hook_persist_param_defaults_to_false(self):
        # Calling without the new kwarg must keep the historical behaviour.
        event, _ = self._build(output_duration=12.0)
        expected = min(HOOK_TITLE_SECONDS, 12.0)
        assert _ass_ts_to_seconds(event["end"]) == expected

    def test_hook_margin_v_override_applies_to_event(self):
        event, _ = self._build(hook_margin_v_override=328)
        assert int(event["margin_v"]) == 328, (
            f"hook_margin_v_override must set the event MarginV, got {event['margin_v']!r}"
        )

    def test_hook_margin_v_default_is_zero(self):
        event, _ = self._build()
        assert int(event["margin_v"]) == 0


class TestBuildAssemblyAiAssSubtitlesHookPersist:
    def _build_ass(self, tmp_path, **kwargs):
        video_path = tmp_path / "no_sidecar.mp4"
        ass_path = tmp_path / "captions.ass"
        params = dict(
            video_path=video_path,
            clip_start=0.5,
            clip_end=12.5,
            video_width=1080,
            video_height=1920,
            output_ass_path=ass_path,
            hook_title="X",
        )
        params.update(kwargs)
        ok = video_utils.build_assemblyai_ass_subtitles(**params)
        assert ok is True, "hook-only ASS build returned False"
        assert ass_path.exists()
        return _hook_events_from_ass(ass_path)

    def test_persist_hook_ends_at_output_duration(self, tmp_path):
        events = self._build_ass(tmp_path, hook_persist=True)
        assert len(events) == 1, f"expected one hook event, got {events!r}"
        # output_duration = clip_end - clip_start = 12.0
        assert _ass_ts_to_seconds(events[0]["end"]) == 12.0, (
            f"persisted hook must end at output_duration, got {events[0]['end']!r}"
        )

    def test_default_hook_ends_at_min_hook_title_seconds(self, tmp_path):
        events = self._build_ass(tmp_path)
        assert len(events) == 1, f"expected one hook event, got {events!r}"
        assert _ass_ts_to_seconds(events[0]["end"]) == min(HOOK_TITLE_SECONDS, 12.0)

    def test_hook_margin_v_override_applies_to_event(self, tmp_path):
        events = self._build_ass(tmp_path, hook_margin_v_override=328)
        assert len(events) == 1, f"expected one hook event, got {events!r}"
        assert int(events[0]["margin_v"]) == 328, (
            f"hook_margin_v_override must reach the ASS event, got {events[0]['margin_v']!r}"
        )
