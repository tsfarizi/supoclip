"""
Persistent YouTube source-video cache keyed by canonical video ID.

A cache entry is a video file plus a JSON sidecar recording when it was
fetched, its duration and resolution, and its last access time. Lookups
validate the file (ffprobe duration > 0, non-trivial size) and its TTL;
stores are atomic (write to a temp sibling, then os.replace) so concurrent
readers never observe a partial file. Eviction is LRU by last_access against
VIDEO_CACHE_MAX_GB. Every public function degrades to the working file and
never raises into the caller: the cache is an accelerator, not a gate.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import os
import shutil
import subprocess
import time

from .config import get_config

logger = logging.getLogger(__name__)

CACHE_SUFFIX = ".mp4"
SIDECAR_SUFFIX = ".json"
MIN_VALID_SIZE_BYTES = 1024
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


class VideoCacheEntry:
    """A validated cache hit: the video path plus cached metadata."""

    __slots__ = ("path", "duration_seconds", "height", "width", "fetched_at")

    def __init__(
        self,
        path: Path,
        duration_seconds: Optional[float],
        height: Optional[int],
        width: Optional[int],
        fetched_at: float,
    ):
        self.path = path
        self.duration_seconds = duration_seconds
        self.height = height
        self.width = width
        self.fetched_at = fetched_at


def cache_dir() -> Path:
    """Resolve and create the cache directory (lazy)."""
    config = get_config()
    configured = config.video_cache_dir
    root = Path(configured) if configured else Path(config.temp_dir) / "video_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _video_path(video_id: str) -> Path:
    return cache_dir() / f"{video_id}{CACHE_SUFFIX}"


def _sidecar_path(video_id: str) -> Path:
    return cache_dir() / f"{video_id}{SIDECAR_SUFFIX}"


def _probe_duration(path: Path) -> Optional[float]:
    """Return video duration in seconds via ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except Exception:
        return None


def _probe_size(path: Path) -> Tuple[Optional[int], Optional[int]]:
    """Return (width, height) via ffprobe, or (None, None) on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or "x" not in result.stdout:
            return None, None
        lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
        last = lines[-1]
        width_str, height_str = last.split("x", 1)
        return int(width_str), int(height_str)
    except Exception:
        return None, None


def _read_sidecar(video_id: str) -> Optional[Dict[str, Any]]:
    sidecar = _sidecar_path(video_id)
    if not sidecar.exists():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Video cache sidecar unreadable for %s: %s", video_id, exc)
        return None
    return payload


def _write_sidecar(video_id: str, payload: Dict[str, Any]) -> None:
    sidecar = _sidecar_path(video_id)
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, sidecar)


def _touch_last_access(video_id: str) -> None:
    payload = _read_sidecar(video_id)
    if payload is None:
        return
    payload["last_access"] = time.time()
    try:
        _write_sidecar(video_id, payload)
    except Exception as exc:
        logger.warning("Failed to update video cache access time for %s: %s", video_id, exc)


def _read_entries() -> List[Tuple[str, float, float]]:
    """Return (video_id, last_access, size_bytes) for every cached entry."""
    entries: List[Tuple[str, float, float]] = []
    for sidecar in cache_dir().glob(f"*{SIDECAR_SUFFIX}"):
        video_id = sidecar.name[: -len(SIDECAR_SUFFIX)]
        video_file = _video_path(video_id)
        if not video_file.exists():
            continue
        payload = _read_sidecar(video_id)
        last_access = float((payload or {}).get("last_access", 0) or 0)
        if last_access <= 0:
            last_access = video_file.stat().st_mtime
        entries.append((video_id, last_access, video_file.stat().st_size))
    return entries


def lookup(video_id: str) -> Optional[VideoCacheEntry]:
    """Return a validated cache entry for video_id, or None (miss/corrupt/expired)."""
    config = get_config()
    if not config.video_cache_enabled:
        return None

    video_file = _video_path(video_id)
    if not video_file.exists() or not video_file.is_file():
        return None
    if video_file.stat().st_size < MIN_VALID_SIZE_BYTES:
        invalidate(video_id)
        return None

    payload = _read_sidecar(video_id)
    if payload is None:
        invalidate(video_id)
        return None

    fetched_at = float(payload.get("fetched_at") or 0)
    ttl_hours = int(config.video_cache_ttl_hours)
    if ttl_hours > 0 and time.time() - fetched_at > ttl_hours * 3600:
        logger.info("Video cache entry expired for %s; refreshing", video_id)
        invalidate(video_id)
        return None

    duration = float(payload.get("duration_seconds") or 0) or None
    if duration is None:
        duration = _probe_duration(video_file)
        if duration is None:
            invalidate(video_id)
            return None

    entry = VideoCacheEntry(
        path=video_file,
        duration_seconds=duration,
        height=int(payload["height"]) if payload.get("height") else None,
        width=int(payload["width"]) if payload.get("width") else None,
        fetched_at=fetched_at,
    )
    _touch_last_access(video_id)
    logger.info("Video cache hit for %s (%.1fs)", video_id, duration)
    return entry


def store(
    source_path: Path,
    video_id: str,
    duration_seconds: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """Copy source_path into the cache atomically and write the sidecar.

    Returns the cached path on success, otherwise the original source_path so
    callers keep a usable file. Never raises.
    """
    config = get_config()
    if not config.video_cache_enabled:
        return source_path

    try:
        if not source_path.exists() or source_path.stat().st_size < MIN_VALID_SIZE_BYTES:
            logger.warning("Refusing to cache invalid source file %s", source_path)
            return source_path

        if duration_seconds is None:
            duration_seconds = _probe_duration(source_path)
        if width is None or height is None:
            probed_w, probed_h = _probe_size(source_path)
            width = width if width is not None else probed_w
            height = height if height is not None else probed_h

        dest = _video_path(video_id)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copy2(source_path, tmp)
        os.replace(tmp, dest)

        _write_sidecar(
            video_id,
            {
                "video_id": video_id,
                "fetched_at": time.time(),
                "last_access": time.time(),
                "duration_seconds": duration_seconds,
                "width": width,
                "height": height,
            },
        )
        evict_if_needed()
        logger.info("Stored video cache entry for %s", video_id)
        return dest
    except Exception as exc:
        logger.warning("Failed to store video cache entry for %s: %s", video_id, exc)
        return source_path


def invalidate(video_id: str) -> None:
    """Remove the cache file and its sidecar, if present. Never raises."""
    for path in (_video_path(video_id), _sidecar_path(video_id)):
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:
            logger.warning("Failed to invalidate cache path %s: %s", path, exc)


def evict_if_needed() -> int:
    """Evict least-recently-accessed entries until under VIDEO_CACHE_MAX_GB."""
    config = get_config()
    max_gb = int(config.video_cache_max_gb or 0)
    if max_gb <= 0:
        return 0
    limit_bytes = max_gb * (1024 ** 3)

    entries = _read_entries()
    total = sum(size for _, _, size in entries)
    if total <= limit_bytes:
        return 0

    entries.sort(key=lambda item: item[1])  # LRU first
    removed = 0
    for video_id, _last_access, size in entries:
        if total <= limit_bytes:
            break
        invalidate(video_id)
        total -= size
        removed += 1
    if removed:
        logger.info("Evicted %d video cache entr%s to stay under %dGB", removed, "y" if removed == 1 else "ies", max_gb)
    return removed


def cached_file_for(video_id: str) -> Optional[Path]:
    """Return the cached video path without validation (probe/cleanup helpers)."""
    if not get_config().video_cache_enabled:
        return None
    path = _video_path(video_id)
    return path if path.exists() else None
