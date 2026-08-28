"""
Backfill script for clip compositions.

Scans GeneratedClip rows where composition_json IS NULL.
Uses clip_source_map / load_clip_source_manifest to find source ranges and hook range.
Reads parent Task settings (font, caption_template, output_format).
Builds Composition and saves to clip.composition_json.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.media.composition import (
    AudioSpec,
    CaptionSpec,
    Composition,
    OutputSpec,
    ReframeMode,
    ReframeSpec,
    SegmentSpec,
    SourceAssetRef,
    SpeedSpec,
)
from ..domain.media.source_map import load_clip_source_manifest, load_clip_source_ranges
from ..models import GeneratedClip, Task

logger = logging.getLogger(__name__)


async def backfill_clip_compositions(db: AsyncSession) -> int:
    """
    Scans GeneratedClip rows where composition_json IS NULL.
    Backfills initial Composition into clip.composition_json.
    Returns count of updated clip records.
    """
    # Fetch clips missing composition_json with parent task details
    query = text("""
        SELECT c.id as clip_id, c.file_path, c.start_time, c.end_time, c.text,
               t.id as task_id, t.source_id, t.source_identity, t.font_family,
               t.font_size, t.font_color, t.caption_template, t.output_format,
               s.type as source_type, s.url as source_url
        FROM generated_clips c
        JOIN tasks t ON c.task_id = t.id
        LEFT JOIN sources s ON t.source_id = s.id
        WHERE c.composition_json IS NULL
    """)

    result = await db.execute(query)
    rows = result.fetchall()
    backfilled_count = 0

    for row in rows:
        clip_id = row.clip_id
        file_path_str = row.file_path
        clip_path = Path(file_path_str) if file_path_str else None

        # Resolve ranges from source map / manifest or clip times
        manifest = load_clip_source_manifest(clip_path) if (clip_path and clip_path.exists()) else None
        if manifest is None and clip_path:
            # Try loading ranges directly
            ranges = load_clip_source_ranges(clip_path)
            if ranges:
                manifest = {"source_ranges": ranges, "main_ranges": ranges, "hook_range": None}

        source_ref = SourceAssetRef(
            source_id=row.source_id,
            source_type=row.source_type,
            source_url=row.source_url,
            source_identity=row.source_identity,
        )

        caption_spec = CaptionSpec(
            text_override=None,
            font_family=row.font_family,
            font_size=row.font_size,
            font_color=row.font_color,
            template=row.caption_template or "default",
            position="bottom",
            highlight_words=[],
        )

        segments: list[SegmentSpec] = []

        if manifest and manifest.get("source_ranges"):
            hook_r = manifest.get("hook_range")
            main_rs = manifest.get("main_ranges") or manifest.get("source_ranges")

            if hook_r:
                segments.append(
                    SegmentSpec(
                        id=f"hook_{clip_id[:8]}",
                        source_start=round(float(hook_r[0]), 3),
                        source_end=round(float(hook_r[1]), 3),
                        reframe=ReframeSpec(mode=ReframeMode.track),
                        speed=SpeedSpec(rate=1.0),
                        audio=AudioSpec(take_source=True, gain_db=0.0),
                        caption=caption_spec.model_copy(),
                    )
                )

            for idx, r in enumerate(main_rs):
                segments.append(
                    SegmentSpec(
                        id=f"seg_{clip_id[:8]}_{idx}",
                        source_start=round(float(r[0]), 3),
                        source_end=round(float(r[1]), 3),
                        reframe=ReframeSpec(mode=ReframeMode.track),
                        speed=SpeedSpec(rate=1.0),
                        audio=AudioSpec(take_source=True, gain_db=0.0),
                        caption=caption_spec.model_copy(),
                    )
                )
        else:
            # Fall back to start_time and end_time strings
            from ..video_utils import parse_timestamp_to_seconds

            s_start = parse_timestamp_to_seconds(str(row.start_time or "00:00"))
            s_end = parse_timestamp_to_seconds(str(row.end_time or "00:00"))
            if s_end <= s_start:
                s_end = s_start + 1.0

            segments.append(
                SegmentSpec(
                    id=f"seg_{clip_id[:8]}",
                    source_start=round(s_start, 3),
                    source_end=round(s_end, 3),
                    reframe=ReframeSpec(mode=ReframeMode.track),
                    speed=SpeedSpec(rate=1.0),
                    audio=AudioSpec(take_source=True, gain_db=0.0),
                    caption=caption_spec.model_copy(),
                )
            )

        output_spec = OutputSpec(
            format=row.output_format or "vertical",
            preset="tiktok",
        )

        comp = Composition(
            schema_version=1,
            source_asset_ref=source_ref,
            output=output_spec,
            segments=segments,
            broll_inserts=[],
            sfx=[],
            soundtrack=None,
            transitions=[],
        )

        comp_json = comp.to_json()

        update_query = text("""
            UPDATE generated_clips
            SET composition_json = :comp_json,
                composition_version = 1,
                updated_at = NOW()
            WHERE id = :clip_id
        """)
        await db.execute(update_query, {"comp_json": comp_json, "clip_id": clip_id})
        backfilled_count += 1

    await db.commit()
    logger.info("Backfilled %d clip compositions", backfilled_count)
    return backfilled_count
