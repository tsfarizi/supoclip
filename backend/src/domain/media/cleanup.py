"""
Clip cleanup helpers: settings normalization and orphan file reconciliation.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_TTL_SECONDS = 3600  # 1 hour

DEFAULT_PAUSE_THRESHOLD_MS = 900
DEFAULT_FILTERED_WORDS = [
    "um",
    "uh",
    "erm",
    "hmm",
    "mm",
    "you know",
    "i mean",
    "sort of",
    "kind of",
]


def normalize_pause_threshold_ms(
    value: Any, default: int = DEFAULT_PAUSE_THRESHOLD_MS
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(250, min(3000, parsed))


def normalize_filtered_words(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.strip().lower().split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def normalize_clip_cleanup_settings(
    cut_long_pauses: Any = False,
    pause_threshold_ms: Any = DEFAULT_PAUSE_THRESHOLD_MS,
    remove_filler_words: Any = False,
    filtered_words: Any = None,
) -> dict[str, Any]:
    return {
        "cut_long_pauses": bool(cut_long_pauses),
        "pause_threshold_ms": normalize_pause_threshold_ms(pause_threshold_ms),
        "remove_filler_words": bool(remove_filler_words),
        "filtered_words": normalize_filtered_words(filtered_words),
    }


def clip_cleanup_enabled(settings: dict[str, Any] | None) -> bool:
    if not settings:
        return False
    return bool(
        settings.get("cut_long_pauses")
        or settings.get("remove_filler_words")
        or settings.get("filtered_words")
    )


def clip_sidecar_paths(clip_path: Path) -> list[Path]:
    """Sidecar siblings that accompany a rendered clip file.

    The source-map sidecar doubles as the hook manifest; the SFX sidecar
    records sound-effect placements. Both are keyed off the mp4 stem.
    """
    return [
        clip_path.with_suffix(".source_map.json"),
        clip_path.with_suffix(".sfx.json"),
    ]


def _normalize_path(value: str | Path) -> str:
    # resolve() makes the path absolute (and case-true on disk for existing
    # files); normcase() folds Windows case so DB-stored and scanned paths
    # compare equal regardless of stored casing.
    return os.path.normcase(str(Path(value).resolve()))


def _remove_clip_and_sidecars(clip_path: Path) -> None:
    for path in [clip_path, *clip_sidecar_paths(clip_path)]:
        path.unlink(missing_ok=True)


async def reconcile_orphaned_clip_files(
    db: AsyncSession,
    temp_dir: str | Path | None = None,
    *,
    ttl_seconds: int = DEFAULT_ORPHAN_TTL_SECONDS,
) -> dict[str, Any]:
    """Remove rendered clip mp4s that no DB row references and are past the TTL.

    Scans ``<temp_dir>/clips`` recursively for ``*.mp4``, keeps every path
    listed in ``generated_clips.file_path``, and deletes the rest once their
    mtime is older than ``ttl_seconds`` together with their sidecar siblings
    (``.source_map.json``, ``.sfx.json``). The TTL protects renders that are
    mid-flight: a fresh output is younger than the TTL until its row commits,
    and a row that never commits leaves a file that is reclaimed only later.

    Returns a summary dict: scanned / referenced / removed / failed.
    """
    from ...shared.config import get_config

    clips_dir = Path(temp_dir or get_config().temp_dir) / "clips"
    if not clips_dir.is_dir():
        return {"scanned": 0, "referenced": 0, "removed": 0, "failed": 0}

    result = await db.execute(sa_text("SELECT file_path FROM generated_clips"))
    referenced = {
        _normalize_path(row[0]) for row in result.fetchall() if row[0]
    }

    cutoff = time.time() - max(0, int(ttl_seconds))
    scanned = 0
    removed = 0
    failed = 0
    for candidate in clips_dir.rglob("*.mp4"):
        if not candidate.is_file():
            continue
        scanned += 1
        if _normalize_path(candidate) in referenced:
            continue
        try:
            if candidate.stat().st_mtime > cutoff:
                continue
        except OSError:
            failed += 1
            continue
        try:
            _remove_clip_and_sidecars(candidate)
            removed += 1
        except OSError as exc:
            # Windows WinError 32: file is still being served/streamed (frontend
            # preview holds an open handle). Treat as transient — skip without
            # counting as a permanent failure so the hourly cron doesn't spam.
            err_no = getattr(exc, "winerror", None)
            if err_no == 32 or "being used by another process" in str(exc):
                logger.info("Skipping locked orphan clip %s (in use)", candidate)
                continue
            failed += 1
            logger.warning("Failed to remove orphan clip %s: %s", candidate, exc)
    return {
        "scanned": scanned,
        "referenced": len(referenced),
        "removed": removed,
        "failed": failed,
    }
