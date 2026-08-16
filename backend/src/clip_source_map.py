"""
Helpers for persisting a clip's mapping back to source-video time ranges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple
import json
import logging

logger = logging.getLogger(__name__)

SourceRange = Tuple[float, float]
SOURCE_MAP_VERSION = 2
MIN_RANGE_SECONDS = 0.05


def clip_source_map_path(clip_path: Path) -> Path:
    return clip_path.with_suffix(".source_map.json")


def normalize_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
) -> List[SourceRange]:
    normalized: List[SourceRange] = []
    for start, end in ranges or []:
        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError):
            continue
        if end_value - start_value <= MIN_RANGE_SECONDS:
            continue
        normalized.append((start_value, end_value))
    return normalized


def total_source_duration(ranges: Iterable[tuple[float, float]] | None) -> float:
    return sum(end - start for start, end in normalize_source_ranges(ranges))


def source_range_bounds(
    ranges: Iterable[tuple[float, float]] | None,
) -> SourceRange | None:
    normalized = normalize_source_ranges(ranges)
    if not normalized:
        return None
    return (normalized[0][0], normalized[-1][1])


def save_clip_source_ranges(
    clip_path: Path,
    ranges: Iterable[tuple[float, float]] | None,
) -> None:
    normalized = normalize_source_ranges(ranges)
    path = clip_source_map_path(clip_path)
    if not normalized:
        path.unlink(missing_ok=True)
        return

    payload = {
        "version": SOURCE_MAP_VERSION,
        "source_ranges": [
            {"start": round(start, 6), "end": round(end, 6)}
            for start, end in normalized
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def save_clip_source_manifest(
    clip_path: Path,
    main_ranges: Iterable[tuple[float, float]] | None,
    hook_range: tuple[float, float] | None = None,
) -> None:
    """Persist source ranges plus the private hook role used by editor merges."""
    normalized_main = normalize_source_ranges(main_ranges)
    normalized_hook = normalize_source_ranges([hook_range] if hook_range else None)
    path = clip_source_map_path(clip_path)
    if not normalized_main:
        path.unlink(missing_ok=True)
        return
    payload = {
        "version": SOURCE_MAP_VERSION,
        "source_ranges": [
            {"start": round(start, 6), "end": round(end, 6)}
            for start, end in ([*normalized_hook, *normalized_main])
        ],
        "roles": {
            "hook": (
                {"start": round(normalized_hook[0][0], 6), "end": round(normalized_hook[0][1], 6)}
                if normalized_hook else None
            ),
            "main": [
                {"start": round(start, 6), "end": round(end, 6)}
                for start, end in normalized_main
            ],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_clip_source_manifest(clip_path: Path) -> dict | None:
    """Read role metadata while accepting the original ranges-only sidecar."""
    path = clip_source_map_path(clip_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read clip source manifest %s: %s", path, exc)
        return None
    ranges = load_clip_source_ranges(clip_path)
    if not ranges:
        return None
    roles = payload.get("roles") if isinstance(payload, dict) else None
    hook = None
    if isinstance(roles, dict) and isinstance(roles.get("hook"), dict):
        item = roles["hook"]
        parsed = normalize_source_ranges([(item.get("start"), item.get("end"))])
        hook = parsed[0] if parsed else None
    main = ranges
    if isinstance(roles, dict) and isinstance(roles.get("main"), list):
        parsed_main = normalize_source_ranges(
            [(item.get("start"), item.get("end")) for item in roles["main"] if isinstance(item, dict)]
        )
        if parsed_main:
            main = parsed_main
    return {"source_ranges": ranges, "main_ranges": main, "hook_range": hook}


def load_clip_source_ranges(clip_path: Path) -> List[SourceRange] | None:
    path = clip_source_map_path(clip_path)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read clip source map %s: %s", path, exc)
        return None

    raw_ranges = payload.get("source_ranges")
    if not isinstance(raw_ranges, list):
        return None

    parsed_ranges: List[SourceRange] = []
    for item in raw_ranges:
        if not isinstance(item, dict):
            continue
        if "start" not in item or "end" not in item:
            continue
        parsed_ranges.append((item["start"], item["end"]))

    normalized = normalize_source_ranges(parsed_ranges)
    return normalized or None


def copy_clip_source_ranges(source_path: Path, target_path: Path) -> None:
    ranges = load_clip_source_ranges(source_path)
    if ranges is None:
        clip_source_map_path(target_path).unlink(missing_ok=True)
        return
    save_clip_source_ranges(target_path, ranges)


def slice_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
    output_start: float,
    output_end: float,
) -> List[SourceRange]:
    normalized = normalize_source_ranges(ranges)
    if not normalized:
        return []

    total_duration = total_source_duration(normalized)
    window_start = max(0.0, min(float(output_start), total_duration))
    window_end = max(window_start, min(float(output_end), total_duration))

    sliced: List[SourceRange] = []
    timeline_cursor = 0.0
    for source_start, source_end in normalized:
        segment_duration = source_end - source_start
        timeline_start = timeline_cursor
        timeline_end = timeline_cursor + segment_duration

        overlap_start = max(window_start, timeline_start)
        overlap_end = min(window_end, timeline_end)
        if overlap_end - overlap_start > MIN_RANGE_SECONDS:
            relative_start = overlap_start - timeline_start
            relative_end = overlap_end - timeline_start
            sliced.append(
                (source_start + relative_start, source_start + relative_end)
            )

        timeline_cursor = timeline_end

    return normalize_source_ranges(sliced)


def trim_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
    start_offset: float,
    end_offset: float,
) -> List[SourceRange]:
    total_duration = total_source_duration(ranges)
    return slice_source_ranges(
        ranges,
        max(0.0, float(start_offset)),
        max(0.0, total_duration - max(0.0, float(end_offset))),
    )


def split_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
    split_time: float,
) -> tuple[List[SourceRange], List[SourceRange]]:
    total_duration = total_source_duration(ranges)
    split_point = max(0.0, min(float(split_time), total_duration))
    return (
        slice_source_ranges(ranges, 0.0, split_point),
        slice_source_ranges(ranges, split_point, total_duration),
    )
