"""
Composition Builder.

Builds initial Composition domain models from AI analysis outputs
(TranscriptSegment, HookSelection, BRollOpportunity, SoundEffectOpportunity).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union
import uuid

from .composition import (
    AudioSpec,
    BrollInsertSpec,
    CaptionSpec,
    Composition,
    OutputSpec,
    ReframeMode,
    ReframeSpec,
    SegmentSpec,
    SoundFxSpec,
    SourceAssetRef,
    SpeedSpec,
)

logger = logging.getLogger(__name__)


def _parse_time_str(val: str) -> float:
    """Parse MM:SS or HH:MM:SS string to seconds without importing video_utils at top level."""
    try:
        parts = val.strip().split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1:
            return float(parts[0])
    except Exception:
        pass
    return 0.0


def _to_seconds(val: Any) -> float:
    """Convert string MM:SS / HH:MM:SS or number to float seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        return _parse_time_str(val)
    return 0.0


def _get_field(obj: Any, field_name: str, default: Any = None) -> Any:
    """Get field from dict or pydantic model."""
    if isinstance(obj, dict):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


class CompositionBuilder:
    """Builds Composition domain models from transcript segments and opportunities."""

    @staticmethod
    def build_from_segment(
        segment: Any,  # TranscriptSegment or dict
        source_ref: SourceAssetRef,
        output_format: str = "vertical",
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        broll_opps: Optional[list[Any]] = None,
        sfx_opps: Optional[list[Any]] = None,
    ) -> Composition:
        """
        Creates initial Composition for a clip from AI analysis output:
        - Maps hook range (if present) + main segment range into `segments`.
        - Maps any matching B-roll opportunities within this segment's time bounds into `broll_inserts`.
        - Maps any matching SFX opportunities within this segment's time bounds into `sfx`.
        - Sets default reframe (mode=track), speed (1.0), audio (take_source=True, gain_db=0.0), caption (style from task).
        """
        # Parse main segment bounds
        start_raw = _get_field(segment, "start_time", "00:00")
        end_raw = _get_field(segment, "end_time", "00:00")
        main_start = _to_seconds(start_raw)
        main_end = _to_seconds(end_raw)
        if main_end <= main_start:
            main_end = main_start + 1.0

        # Caption spec
        text = _get_field(segment, "text", "")
        caption_spec = CaptionSpec(
            text_override=None,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            template=caption_template or "default",
            position="bottom",
            highlight_words=[],
        )

        segments_list: List[SegmentSpec] = []

        # Hook selection (if present)
        hook = _get_field(segment, "hook_selection")
        if hook:
            hook_start_raw = _get_field(hook, "hook_start_time")
            hook_end_raw = _get_field(hook, "hook_end_time")
            if hook_start_raw is not None and hook_end_raw is not None:
                h_start = _to_seconds(hook_start_raw)
                h_end = _to_seconds(hook_end_raw)
                if h_end > h_start:
                    segments_list.append(
                        SegmentSpec(
                            id=f"hook_{uuid.uuid4().hex[:8]}",
                            source_start=round(h_start, 3),
                            source_end=round(h_end, 3),
                            reframe=ReframeSpec(mode=ReframeMode.track),
                            speed=SpeedSpec(rate=1.0),
                            audio=AudioSpec(take_source=True, gain_db=0.0),
                            caption=caption_spec.model_copy(),
                        )
                    )

        # Main segment
        segments_list.append(
            SegmentSpec(
                id=f"seg_{uuid.uuid4().hex[:8]}",
                source_start=round(main_start, 3),
                source_end=round(main_end, 3),
                reframe=ReframeSpec(mode=ReframeMode.track),
                speed=SpeedSpec(rate=1.0),
                audio=AudioSpec(take_source=True, gain_db=0.0),
                caption=caption_spec.model_copy(),
            )
        )

        # Overall clip bounds for matching opportunities
        clip_source_start = segments_list[0].source_start
        clip_source_end = segments_list[-1].source_end

        # Map B-roll opportunities
        broll_inserts: List[BrollInsertSpec] = []
        raw_broll = broll_opps or _get_field(segment, "broll_opportunities") or _get_field(segment, "broll_suggestions") or []
        for b_item in raw_broll:
            b_ts = _to_seconds(_get_field(b_item, "timestamp", _get_field(b_item, "source_timestamp", "00:00")))
            b_dur = float(_get_field(b_item, "duration", 3.0))
            b_term = str(_get_field(b_item, "search_term", _get_field(b_item, "query", "broll")))
            b_path = _get_field(b_item, "local_path", _get_field(b_item, "asset_path"))

            # If timestamp falls within the clip's source time window
            if clip_source_start <= b_ts <= clip_source_end:
                # Relative time within rendered clip
                rel_at_time = max(0.0, b_ts - clip_source_start)
                broll_inserts.append(
                    BrollInsertSpec(
                        id=f"broll_{uuid.uuid4().hex[:8]}",
                        at_time=round(rel_at_time, 3),
                        duration=round(b_dur, 3),
                        search_term=b_term,
                        asset_path=str(b_path) if b_path else None,
                        reframe=ReframeSpec(mode=ReframeMode.track),
                    )
                )

        # Map SFX opportunities
        sfx_inserts: List[SoundFxSpec] = []
        raw_sfx = sfx_opps or _get_field(segment, "sfx_opportunities") or []
        for s_item in raw_sfx:
            s_ts = _to_seconds(_get_field(s_item, "source_timestamp", _get_field(s_item, "timestamp", "00:00")))
            s_dur = float(_get_field(s_item, "duration", 1.0))
            s_query = str(_get_field(s_item, "query", _get_field(s_item, "sound_effect", "whoosh")))
            s_intensity = str(_get_field(s_item, "intensity", "moderate"))
            s_gain = float(_get_field(s_item, "gain_db", 0.0))
            s_place = str(_get_field(s_item, "placement", "main"))
            s_path = _get_field(s_item, "asset_path", _get_field(s_item, "local_path"))

            if clip_source_start <= s_ts <= clip_source_end:
                rel_at_time = max(0.0, s_ts - clip_source_start)
                sfx_inserts.append(
                    SoundFxSpec(
                        id=f"sfx_{uuid.uuid4().hex[:8]}",
                        at_time=round(rel_at_time, 3),
                        duration=round(s_dur, 3),
                        query=s_query,
                        intensity=s_intensity,
                        gain_db=round(s_gain, 2),
                        placement=s_place,
                        asset_path=str(s_path) if s_path else None,
                    )
                )

        output_spec = OutputSpec(
            format=output_format or "vertical",
            preset="tiktok",
        )

        return Composition(
            schema_version=1,
            source_asset_ref=source_ref,
            output=output_spec,
            segments=segments_list,
            broll_inserts=broll_inserts,
            sfx=sfx_inserts,
            soundtrack=None,
            transitions=[],
        )
