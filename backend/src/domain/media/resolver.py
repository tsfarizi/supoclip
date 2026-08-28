"""
Source asset resolver for editable compositions.
Resolves SourceAssetRef references to local video file paths on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...infra.db.repositories.cache_repository import CacheRepository
from ...infra.db.repositories.source_repository import SourceRepository
from .composition import SourceAssetRef

logger = logging.getLogger(__name__)


def _resolve_upload_path(url: str) -> Path:
    """Resolve upload:// URLs to temp upload path without importing VideoService at module level."""
    from ...config import get_config
    from ...services.video_service import UPLOAD_URL_PREFIX
    if url.startswith(UPLOAD_URL_PREFIX):
        filename = Path(url.removeprefix(UPLOAD_URL_PREFIX)).name
        return Path(get_config().temp_dir) / "uploads" / filename
    raise ValueError("Only upload:// references are allowed for local video sources")


class SourceUnavailableError(Exception):
    """Raised when a source asset reference cannot be resolved or does not exist on disk."""
    pass


class SourceAssetResolver:
    """Resolves SourceAssetRef to a local file path on disk."""

    @staticmethod
    async def resolve(db: AsyncSession, ref: SourceAssetRef) -> Path:
        """
        Resolve source asset to local file path using:
        1. ProcessingCache (by source_identity, source_url, or source_id)
        2. Source table (by source_id -> url -> ProcessingCache / VideoService)
        3. VideoService.resolve_local_video_path for upload:// references
        4. VideoCache on disk if applicable

        If file does not exist on disk or source cannot be resolved, raises SourceUnavailableError.

        Constraint: Path.is_file() calls are synchronous but acceptable here —
        all resolved paths target local fast disk (temp_dir / video_cache_dir).
        The stat cost is negligible vs the subsequent ffmpeg render.
        """
        if not ref:
            raise SourceUnavailableError("Source video is no longer available on disk. Please re-ingest or re-upload.")

        candidate_path: Optional[Path] = None

        # 1. Check direct source_url if it's an upload:// URL
        if ref.source_url and ref.source_url.startswith("upload://"):
            try:
                candidate_path = _resolve_upload_path(ref.source_url)
            except Exception as e:
                logger.debug("Failed resolving local video path for %s: %s", ref.source_url, e)

        # 2. Check ProcessingCache by source_identity / source_url / source_id
        if not (candidate_path and candidate_path.is_file()):
            cache_keys_to_try = [k for k in [ref.source_identity, ref.source_url, ref.source_id] if k]
            for ck in cache_keys_to_try:
                cache_entry = await CacheRepository.get_cache(db, ck)
                if cache_entry and cache_entry.get("video_path"):
                    p = Path(cache_entry["video_path"])
                    if p.is_file():
                        candidate_path = p
                        break

        # 3. If still not resolved and source_id is present, look up Source in DB
        if not (candidate_path and candidate_path.is_file()) and ref.source_id:
            source = await SourceRepository.get_source_by_id(db, ref.source_id)
            if source and source.get("url"):
                src_url = source["url"]
                if src_url.startswith("upload://"):
                    try:
                        p = _resolve_upload_path(src_url)
                        if p.is_file():
                            candidate_path = p
                    except Exception:
                        pass
                if not (candidate_path and candidate_path.is_file()):
                    cache_entry = await CacheRepository.get_cache(db, src_url)
                    if cache_entry and cache_entry.get("video_path"):
                        p = Path(cache_entry["video_path"])
                        if p.is_file():
                            candidate_path = p

        # 4. Check video cache / temp paths if source_identity has a youtube ID
        if not (candidate_path and candidate_path.is_file()):
            identity = ref.source_identity or (ref.source_url if ref.source_url and "youtu" in ref.source_url else None)
            if identity:
                # Extract potential youtube ID
                vid = identity.removeprefix("youtube:")
                from ...config import get_config
                cfg = get_config()
                # Check video cache directory if configured or default data/video_cache
                cache_dirs = [
                    Path(cfg.video_cache_dir) if cfg.video_cache_dir else None,
                    Path("data/video_cache"),
                    Path("temp/video_cache"),
                    Path("data"),
                    Path("temp"),
                ]
                for cdir in filter(None, cache_dirs):
                    if cdir.exists():
                        possible_file = cdir / f"{vid}.mp4"
                        if possible_file.is_file():
                            candidate_path = possible_file
                            break

        if candidate_path and candidate_path.is_file():
            return candidate_path.resolve()

        raise SourceUnavailableError("Source video is no longer available on disk. Please re-ingest or re-upload.")
