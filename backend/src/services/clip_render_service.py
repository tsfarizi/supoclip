"""
Clip render service - clip editing operations extracted from TaskService.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
from pathlib import Path
import json
import hashlib
import logging

from ..repositories.task_repository import TaskRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.cache_repository import CacheRepository
from .video_service import VideoService
from ..config import Config, get_config
from ..infra.redis_client import get_sync_redis_client
from ..clip_cleanup import clip_sidecar_paths
from ..clip_source_map import (
    copy_clip_source_ranges,
    load_clip_source_ranges,
    save_clip_source_ranges,
    source_range_bounds,
    split_source_ranges,
    total_source_duration,
    trim_source_ranges,
    load_clip_source_manifest,
)
from ..transition_spec import normalize_transition_spec
from ..video_utils import parse_timestamp_to_seconds
from ..video_utils import create_optimized_clip
from ..utils.async_helpers import run_in_thread
from ..ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION

logger = logging.getLogger(__name__)


class ClipRenderService:
    """Service for clip rendering operations."""

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()
        # When composed into a TaskService, the owner's collaborators are
        # resolved lazily so later attribute swaps on the owner stay visible.
        self.task_service = None
        self._task_repo = TaskRepository()
        self._clip_repo = ClipRepository()
        self._cache_repo = CacheRepository()
        self._video_service = VideoService()

    @property
    def task_repo(self):
        if self.task_service is not None:
            return self.task_service.task_repo
        return self._task_repo

    @property
    def clip_repo(self):
        if self.task_service is not None:
            return self.task_service.clip_repo
        return self._clip_repo

    @property
    def cache_repo(self):
        if self.task_service is not None:
            return self.task_service.cache_repo
        return self._cache_repo

    @property
    def video_service(self):
        if self.task_service is not None:
            return self.task_service.video_service
        return self._video_service

    @staticmethod
    def _remove_superseded_clip(old_path: Path, new_path: Path) -> None:
        """Drop a replaced clip file and its sidecars once the row commit points at new_path."""
        try:
            if not old_path.exists():
                return
            if old_path.resolve() == new_path.resolve():
                return
            for path in [old_path, *clip_sidecar_paths(old_path)]:
                path.unlink(missing_ok=True)
        except Exception:
            logger.debug(
                "Could not remove superseded clip file %s", old_path, exc_info=True
            )

    @staticmethod
    def _build_cache_key(
        url: str, source_type: str, processing_mode: str, include_broll: bool = False
    ) -> str:
        payload = (
            f"{source_type}|{processing_mode}|{int(include_broll)}|"
            f"{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url.strip()}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _seconds_to_mmss(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        minutes = total // 60
        secs = total % 60
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _get_clip_source_ranges(clip: Dict[str, Any]) -> list[tuple[float, float]]:
        file_path = clip.get("file_path")
        if isinstance(file_path, str) and file_path:
            manifest = load_clip_source_manifest(Path(file_path))
            persisted = (manifest or {}).get("main_ranges") if manifest else None
            if not persisted:
                persisted = load_clip_source_ranges(Path(file_path))
            if persisted:
                return persisted

        start_seconds = parse_timestamp_to_seconds(clip["start_time"])
        end_seconds = parse_timestamp_to_seconds(clip["end_time"])
        return [(start_seconds, end_seconds)]

    async def trim_clip(
        self,
        task_id: str,
        clip_id: str,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        # Resolved through task_service at call time: characterization tests
        # monkeypatch task_service.trim_clip_file, and task_service imports
        # this module first, so a module-level import here would deadlock.
        from .task_service import trim_clip_file

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = Path(clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        output_path = trim_clip_file(
            input_path, Path(self.config.temp_dir) / "clips", start_offset, end_offset
        )
        source_ranges = self._get_clip_source_ranges(clip)
        trimmed_ranges = trim_source_ranges(source_ranges, start_offset, end_offset)
        clip_duration = max(0.1, total_source_duration(trimmed_ranges))
        bounds = source_range_bounds(trimmed_ranges)
        if not bounds:
            raise ValueError("Trimmed clip has no remaining source mapping")
        start_seconds, end_seconds = bounds
        save_clip_source_ranges(output_path, trimmed_ranges)

        new_start = self._seconds_to_mmss(start_seconds)
        new_end = self._seconds_to_mmss(end_seconds)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            new_start,
            new_end,
            clip_duration,
            clip.get("text") or "",
        )
        # Phase 2: the superseded input is dropped only after the row has
        # committed pointing at the trimmed file.
        self._remove_superseded_clip(input_path, output_path)
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}

    async def split_clip(
        self, task_id: str, clip_id: str, split_time: float
    ) -> Dict[str, Any]:
        from .task_service import split_clip_file

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = Path(clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        first_path, second_path = split_clip_file(
            input_path, Path(self.config.temp_dir) / "clips", split_time
        )

        clamped_split = max(0.2, min(split_time, float(clip["duration"]) - 0.2))
        source_ranges = self._get_clip_source_ranges(clip)
        first_ranges, second_ranges = split_source_ranges(source_ranges, clamped_split)
        first_bounds = source_range_bounds(first_ranges)
        second_bounds = source_range_bounds(second_ranges)
        if not first_bounds or not second_bounds:
            raise ValueError("Split clip has invalid source mapping")
        save_clip_source_ranges(first_path, first_ranges)
        save_clip_source_ranges(second_path, second_ranges)
        first_duration = max(0.1, total_source_duration(first_ranges))
        second_duration = max(0.1, total_source_duration(second_ranges))

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            first_path.name,
            str(first_path),
            self._seconds_to_mmss(first_bounds[0]),
            self._seconds_to_mmss(first_bounds[1]),
            first_duration,
            clip.get("text") or "",
        )

        await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=second_path.name,
            file_path=str(second_path),
            start_time=self._seconds_to_mmss(second_bounds[0]),
            end_time=self._seconds_to_mmss(second_bounds[1]),
            duration=second_duration,
            text=clip.get("text") or "",
            relevance_score=clip.get("relevance_score", 0.5),
            reasoning=clip.get("reasoning") or "Split from original clip",
            clip_order=clip.get("clip_order", 1) + 1,
            virality_score=clip.get("virality_score", 0),
            hook_score=clip.get("hook_score", 0),
            engagement_score=clip.get("engagement_score", 0),
            value_score=clip.get("value_score", 0),
            shareability_score=clip.get("shareability_score", 0),
            hook_type=clip.get("hook_type"),
            hook_title=clip.get("hook_title"),
        )

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        # Phase 2: the original input is replaced by the two split files; drop it
        # only after both rows have committed (update + create).
        self._remove_superseded_clip(input_path, first_path)
        return {"message": "Clip split successfully"}

    async def merge_clips(
        self,
        task_id: str,
        clip_ids: list[str],
        transition: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Merge multiple clips into one, optionally rendering inter-clip transitions."""
        from .task_service import apply_transitions_between_clips, merge_clip_files

        if len(clip_ids) < 2:
            raise ValueError("At least two clips are required to merge")

        clips = []
        for clip_id in clip_ids:
            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("One or more clips not found")
            clips.append(clip)

        ordered = sorted(clips, key=lambda c: c.get("clip_order", 0))
        paths = [Path(c["file_path"]) for c in ordered]

        hook_path = await self._build_editor_hook(ordered[0])

        spec = normalize_transition_spec(transition or "")
        if (spec != "none" or hook_path is not None) and len(paths) >= 2:
            try:
                merged_path = apply_transitions_between_clips(
                    paths,
                    spec,
                    Path(self.config.temp_dir) / "clips",
                    **({"hook_path": hook_path} if hook_path is not None else {}),
                )
            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Transition render failed for task %s spec %s: %s; falling back to hard concat",
                    task_id,
                    spec,
                    e,
                )
                merged_path = merge_clip_files(
                    paths,
                    Path(self.config.temp_dir) / "clips",
                )
        else:
            merged_path = merge_clip_files(
                paths,
                Path(self.config.temp_dir) / "clips",
            )

        merged_ranges = []
        for clip in ordered:
            merged_ranges.extend(self._get_clip_source_ranges(clip))
        merged_bounds = source_range_bounds(merged_ranges)
        if merged_bounds:
            start_time = self._seconds_to_mmss(merged_bounds[0])
            end_time = self._seconds_to_mmss(merged_bounds[1])
            duration = total_source_duration(merged_ranges)
            save_clip_source_ranges(merged_path, merged_ranges)
            if hook_path is not None:
                manifest = load_clip_source_manifest(paths[0])
                if manifest and manifest.get("hook_range"):
                    from ..clip_source_map import save_clip_source_manifest

                    save_clip_source_manifest(
                        merged_path, merged_ranges, manifest["hook_range"]
                    )
        else:
            start_time = ordered[0]["start_time"]
            end_time = ordered[-1]["end_time"]
            duration = sum(float(c.get("duration", 0.0)) for c in ordered)
        text = " ".join((c.get("text") or "").strip() for c in ordered if c.get("text"))

        first = ordered[0]
        await self.clip_repo.update_clip(
            self.db,
            first["id"],
            merged_path.name,
            str(merged_path),
            start_time,
            end_time,
            duration,
            text,
        )

        for clip in ordered[1:]:
            await self.clip_repo.delete_clip(self.db, clip["id"])

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        # Phase 2: every source clip is superseded by the merged file; drop them
        # only after the first row was updated and the rest deleted (all committed).
        for path in paths:
            self._remove_superseded_clip(path, merged_path)
        return {"message": "Clips merged successfully", "clip_id": first["id"]}

    async def _build_editor_hook(self, clip: Dict[str, Any]) -> Optional[Path]:
        """Materialize the first clip's persisted hook for the merge API."""
        manifest = load_clip_source_manifest(Path(clip["file_path"]))
        hook_range = manifest.get("hook_range") if manifest else None
        if not hook_range:
            return None
        task = await self.task_repo.get_task_by_id(self.db, clip["task_id"])
        if not task:
            return None
        try:
            source: Optional[Path] = None
            if task.get("source_type") == "video_url":
                source = self.video_service.resolve_local_video_path(task["source_url"])
            elif task.get("source_type") == "youtube":
                cache = await self.cache_repo.get_cache(
                    self.db,
                    self._build_cache_key(
                        task["source_url"],
                        task["source_type"],
                        task.get("processing_mode") or self.config.default_processing_mode,
                        bool(task.get("include_broll", False)),
                    ),
                )
                cached_path = cache.get("video_path") if cache else None
                source = Path(cached_path) if cached_path else None
            if source is None:
                return None
            if not source.is_file():
                return None
            metadata = task
            output = Path(self.config.temp_dir) / "clips" / (
                f"editor_hook_{clip['id']}.mp4"
            )
            ok = create_optimized_clip(
                source,
                hook_range[0],
                hook_range[1],
                output,
                add_subtitles=metadata.get("add_subtitles", True),
                font_family=metadata.get("font_family"),
                font_size=metadata.get("font_size"),
                font_color=metadata.get("font_color"),
                caption_template=metadata.get("caption_template", "default"),
                output_format=metadata.get("output_format", "vertical"),
                keep_ranges=[hook_range],
                hook_title=clip.get("hook_title"),
                watermark=metadata.get("watermark"),
                watermark_persist=metadata.get("watermark_persist", False),
            )
            return output if ok and output.is_file() else None
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            logger.warning("Unable to materialize editor hook for %s: %s", clip["id"], exc)
            return None

    async def update_clip_captions(
        self,
        task_id: str,
        clip_id: str,
        caption_text: str,
        position: str,
        highlight_words: list[str],
    ) -> Dict[str, Any]:
        from .task_service import overlay_custom_captions

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = Path(clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        transcript_video_path: Optional[Path] = None
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        processing_mode = (
            task.get("processing_mode") or self.config.default_processing_mode
        )
        if source_url and source_type:
            cache_entry = await self.cache_repo.get_cache(
                self.db,
                self._build_cache_key(
                    source_url,
                    source_type,
                    processing_mode,
                    bool(task.get("include_broll", False)),
                ),
            )
            cached_video_path = cache_entry.get("video_path") if cache_entry else None
            if cached_video_path:
                transcript_video_path = Path(cached_video_path)
            elif source_type != "youtube":
                try:
                    transcript_video_path = self.video_service.resolve_local_video_path(
                        source_url
                    )
                except ValueError:
                    transcript_video_path = None

        render_redis = get_sync_redis_client()

        # Serialize re-renders per clip: concurrent caption edits on the same
        # clip used to race (both writing the DB, last-commit-wins) which could
        # leave the clip row pointing at a file that was never persisted.
        lock_key = f"clip_render_lock:{clip_id}"
        lock_acquired = render_redis.set(lock_key, "1", nx=True, ex=600)
        if not lock_acquired:
            raise ValueError(
                "Clip sedang di-render ulang. Tunggu hingga render selesai sebelum menyimpan perubahan caption."
            )

        def publish_render_progress(progress: int, message: str) -> None:
            try:
                render_redis.publish(
                    f"progress:{task_id}",
                    json.dumps(
                        {
                            "task_id": task_id,
                            "clip_id": clip_id,
                            "event_type": "clip_render",
                            "progress": progress,
                            "message": message,
                            "status": "processing",
                        }
                    ),
                )
            except Exception:
                logger.debug(
                    "Failed to publish clip render progress for %s", clip_id, exc_info=True
                )

        try:
            output_path = await run_in_thread(
                overlay_custom_captions,
                input_path,
                Path(self.config.temp_dir) / "clips",
                caption_text,
                position,
                highlight_words,
                font_family=task.get("font_family") or None,
                font_size=task.get("font_size") or None,
                font_color=task.get("font_color") or None,
                caption_template=task.get("caption_template") or "default",
                transcript_video_path=transcript_video_path,
                source_ranges=self._get_clip_source_ranges(clip),
                output_format=task.get("output_format") or "vertical",
                hook_persist=bool(task.get("hook_persist", False)),
                hook_title=clip.get("hook_title"),
                watermark=task.get("watermark"),
                watermark_persist=bool(task.get("watermark_persist", False)),
                progress_callback=publish_render_progress,
            )
            if not output_path.exists():
                raise RuntimeError("Re-render failed: output clip file was not created")
            # Carry the source-range mapping to the new file before the old one
            # is dropped.
            copy_clip_source_ranges(input_path, output_path)
        finally:
            try:
                render_redis.delete(lock_key)
            except Exception:
                pass

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            clip["start_time"],
            clip["end_time"],
            clip["duration"],
            caption_text,
        )
        # Phase 2: the superseded input is dropped only after the row has
        # committed pointing at the re-rendered file.
        self._remove_superseded_clip(input_path, output_path)
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}
