"""
Task service - orchestrates task creation and processing workflow.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, Callable, List
import logging
from datetime import datetime
from pathlib import Path
import json
import hashlib
import re
from time import perf_counter

from ..repositories.task_repository import TaskRepository
from ..repositories.source_repository import SourceRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.cache_repository import CacheRepository
from .billing_service import BillingService
from .clip_render_service import ClipRenderService
from .video_service import VideoService
from .task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from .task_metadata_service import TaskMetadataService
from ..config import Config, get_config
from ..errors import (
    AnalysisError,
    CancelledError,
    DownloadError,
    DuplicateTaskError,
    RenderError,
    TaskProcessingError,
    TranscriptionError,
)
# Retained: characterization tests monkeypatch these names on this module and
# ClipRenderService resolves them through this namespace at call time.
from ..clip_editor import (
    trim_clip_file,
    split_clip_file,
    merge_clip_files,
    overlay_custom_captions,
)
from ..video_utils import parse_timestamp_to_seconds
from ..transition_engine import apply_transitions_between_clips
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..task_validation import clamp_sound_effects_count
from ..ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from ..clip_source_map import (
    load_clip_source_ranges,
    load_clip_source_manifest,
    source_range_bounds,
)

logger = logging.getLogger(__name__)

QUEUED_TASK_TIMEOUT_MESSAGE = (
    "Task timed out while waiting in queue. "
    "Ensure the worker process is running (run.ps1 starts it automatically)."
)


def normalize_video_identity(url: str) -> str:
    """Canonical identity used to detect duplicate in-flight submissions.

    YouTube links that point at the same video (youtu.be vs watch?v=, with or
    without tracking params) collapse to the same identity; everything else is
    compared verbatim (upload paths).
    """
    url = (url or "").strip()
    if not url:
        return ""
    match = re.search(
        r"(?:youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]{6,})", url
    )
    if match:
        return f"youtube:{match.group(1)}"
    return url


class TaskService:
    """Service for task workflow orchestration."""

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.task_repo = TaskRepository()
        self.source_repo = SourceRepository()
        self.clip_repo = ClipRepository()
        self.cache_repo = CacheRepository()
        self.video_service = VideoService()
        self.config = config or get_config()
        # ClipRenderService resolves its collaborators through this owner
        # reference, so the task workflow and clip rendering share repos.
        self.clip_render = ClipRenderService(self.db, self.config)
        self.clip_render.task_service = self
        self.metadata = TaskMetadataService(self.db, self.config)

    @staticmethod
    def _build_cache_key(
        url: str, source_type: str, processing_mode: str, include_broll: bool = False,
        sound_effects_count: int = 0,
    ) -> str:
        payload = (
            f"{source_type}|{processing_mode}|{int(include_broll)}|{max(0, min(5, int(sound_effects_count)))}|"
            f"{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url.strip()}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _is_stale_queued_task(self, task: Dict[str, Any]) -> bool:
        """Detect queued tasks that have likely stalled due to worker issues."""
        if task.get("status") != "queued":
            return False

        created_at = task.get("created_at")
        updated_at = task.get("updated_at") or created_at

        if not created_at or not updated_at:
            return False

        now = (
            datetime.now(updated_at.tzinfo)
            if getattr(updated_at, "tzinfo", None)
            else datetime.utcnow()
        )
        age_seconds = (now - updated_at).total_seconds()
        return age_seconds >= self.config.queued_task_timeout_seconds

    async def create_task_with_source(
        self,
        user_id: str,
        url: str,
        title: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        include_broll: bool = False,
        sound_effects_count: int = 0,
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        hook_persist: bool = False,
        watermark: Optional[str] = None,
        watermark_persist: bool = False,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a new task with associated source.

        The source, the task, and (when monetization is enabled) the billing
        reservation commit in one atomic transaction: the user's billing row is
        locked with ``SELECT ... FOR UPDATE``, then the source and task rows are
        inserted, then the transaction commits once. Concurrent submissions of
        the same video are resolved by the partial unique index on
        ``tasks.source_identity``: the loser raises :class:`DuplicateTaskError`
        and its whole transaction is rolled back.

        Returns the task ID.
        """
        # Validate user exists
        if not await self.task_repo.user_exists(self.db, user_id):
            raise ValueError(f"User {user_id} not found")

        # Determine source type
        source_type = self.video_service.determine_source_type(url)

        # Get or generate title
        if not title:
            if source_type == "youtube":
                title = await self.video_service.get_video_title(url)
            else:
                title = "Uploaded Video"

        source_identity = normalize_video_identity(url) or None

        try:
            # Billing reservation must run inside the same transaction as the
            # inserts: the row lock serializes concurrent creators and the
            # re-count sees every committed task of the window.
            if self.config.monetization_enabled:
                billing_service = BillingService(self.db, self.config)
                await billing_service.assert_can_create_task_locked(user_id)

            # Create source and task with commit=False so one commit at the end
            # makes the reservation + source + task atomic.
            source_id = await self.source_repo.create_source(
                self.db, source_type=source_type, title=title, url=url, commit=False
            )
            task_id = await self.task_repo.create_task(
                self.db,
                user_id=user_id,
                source_id=source_id,
                status="queued",  # Changed from "processing" to "queued"
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                include_broll=include_broll,
                sound_effects_count=sound_effects_count,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                hook_persist=hook_persist,
                watermark=watermark,
                watermark_persist=watermark_persist,
                cleanup_settings_json=cleanup_settings,
                source_identity=source_identity,
                commit=False,
            )
            await self.db.commit()
        except IntegrityError as exc:
            # The partial unique index on source_identity rejected a concurrent
            # duplicate submission (or a leftover non-terminal row with the
            # same identity). Roll back the aborted transaction and surface the
            # domain-level duplicate signal to the route.
            await self.db.rollback()
            logger.info(
                "Duplicate submission rejected for source_identity=%s user=%s",
                source_identity,
                user_id,
            )
            raise DuplicateTaskError(
                "Video ini sedang diproses. "
                "Tunggu hingga selesai sebelum memproses video yang sama lagi."
            ) from exc
        except Exception:
            # Any other failure (billing limit, DB error) must not leave the
            # source/task rows or the billing row lock behind.
            await self.db.rollback()
            raise

        logger.info(f"Created task {task_id} for user {user_id}")
        return task_id

    async def find_active_task_for_source(
        self, url: str
    ) -> Optional[Dict[str, Any]]:
        """Return an in-flight task processing the same video, if any."""
        identity = normalize_video_identity(url)
        if not identity:
            return None
        active_tasks = await self.task_repo.get_active_tasks_with_sources(self.db)
        for task in active_tasks:
            if normalize_video_identity(task.get("source_url") or "") == identity:
                return task
        return None

    async def process_task(
        self,
        task_id: str,
        url: str,
        source_type: str,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        hook_persist: bool = False,
        include_broll: bool = False,
        sound_effects_count: int = 0,
        progress_callback: Optional[Callable] = None,
        should_cancel: Optional[Callable] = None,
        clip_ready_callback: Optional[Callable] = None,
        watermark: Optional[str] = None,
        watermark_persist: bool = False,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a task: download video, analyze, create clips.
        Returns processing results.

        Orchestrates four bounded stages in a fixed order:
        prepare -> pipeline -> (cache upsert) -> render -> notify. Error
        classification is type-driven only: every failure must surface as a
        typed error from the stage that produced it; the generic handler
        persists ``task_error`` without inspecting the message text.
        """
        try:
            logger.info(f"Starting processing for task {task_id}")
            started_at = datetime.utcnow()
            stage_timings: Dict[str, float] = {}

            prepared = await self._prepare_stage(
                task_id=task_id,
                url=url,
                source_type=source_type,
                processing_mode=processing_mode,
                include_broll=include_broll,
                sound_effects_count=sound_effects_count,
            )
            sound_effects_count = prepared["sound_effects_count"]
            cache_key = prepared["cache_key"]
            cache_hit = prepared["cache_hit"]
            cached_transcript = prepared["cached_transcript"]
            cached_analysis_json = prepared["cached_analysis_json"]

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                started_at=started_at,
                cache_hit=cache_hit,
            )

            # Update status to processing. The CAS guard aborts stale worker
            # jobs: a task that left queued/error (cancelled, completed, or
            # already terminal) must never start the pipeline again.
            transitioned = await self.task_repo.update_task_status(
                self.db,
                task_id,
                "processing",
                expected_statuses=["queued", "error"],
                progress=0,
                progress_message="Starting...",
            )
            if not transitioned:
                logger.warning(
                    "Task %s is no longer queued/error; refusing to start processing",
                    task_id,
                )
                raise CancelledError(
                    "Task was cancelled or completed before processing could start"
                )

            # Progress callback wrapper: real-time channel only. The tasks row
            # is a sparse checkpoint log; per-tick writes are dropped so DB
            # traffic scales with checkpoints (start / per-clip / terminal),
            # never with tick volume from the video pipeline.
            async def update_progress(
                progress: int, message: str, status: str = "processing"
            ):
                # Checkpoint pipeline stage progress to DB for polling fallback: without this, polling would stay at "Starting..." for minutes during slow downloads (P3 sparse DB + SSE miss = stuck UI)
                if status != "processing" or progress in (10, 30, 50):
                    try:
                        await self.task_repo.update_task_status(
                            self.db,
                            task_id,
                            status,
                            expected_statuses=["processing"],
                            progress=progress,
                            progress_message=message,
                        )
                    except Exception:
                        pass
                if progress_callback:
                    await progress_callback(progress, message, status)

            # Process video with progress updates
            pipeline_start = perf_counter()
            result = await self._pipeline_stage(
                url=url,
                source_type=source_type,
                task_id=task_id,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                include_broll=include_broll,
                sound_effects_count=sound_effects_count,
                cached_transcript=cached_transcript,
                cached_analysis_json=cached_analysis_json,
                progress_callback=update_progress,
                should_cancel=should_cancel,
            )
            stage_timings["pipeline_seconds"] = round(
                perf_counter() - pipeline_start, 3
            )

            normalized_cleanup_settings = normalize_clip_cleanup_settings(
                **(cleanup_settings or {})
            )

            # Render clips incrementally: render, save, notify one at a time
            segments_to_render = result.get("segments_to_render", [])
            if not segments_to_render:
                await self.cache_repo.upsert_cache(
                    self.db,
                    cache_key=cache_key,
                    source_url=url,
                    source_type=source_type,
                    video_path=result.get("video_path"),
                    transcript_text=result.get("transcript"),
                    analysis_json=None,
                    sound_effects_count=sound_effects_count,
                )
                raise ValueError(
                    "No usable clip segments were selected for this video."
                )

            await self.cache_repo.upsert_cache(
                self.db,
                cache_key=cache_key,
                source_url=url,
                source_type=source_type,
                video_path=result.get("video_path"),
                transcript_text=result.get("transcript"),
                analysis_json=result.get("analysis_json"),
                sound_effects_count=sound_effects_count,
            )

            clip_ids, render_seconds = await self._render_stage(
                task_id=task_id,
                result=result,
                segments_to_render=segments_to_render,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                output_format=output_format,
                add_subtitles=add_subtitles,
                normalized_cleanup_settings=normalized_cleanup_settings,
                hook_persist=hook_persist,
                watermark=watermark,
                watermark_persist=watermark_persist,
                sound_effects_count=sound_effects_count,
                should_cancel=should_cancel,
                update_progress=update_progress,
                clip_ready_callback=clip_ready_callback,
            )
            stage_timings["render_seconds"] = render_seconds

            await self._notify_stage(
                task_id=task_id,
                clip_ids=clip_ids,
                stage_timings=stage_timings,
                progress_callback=progress_callback,
            )

            logger.info(
                f"Task {task_id} completed successfully with {len(clip_ids)} clips"
            )

            return {
                "task_id": task_id,
                "clips_count": len(clip_ids),
                "segments": result["segments"],
                "summary": result.get("summary"),
                "key_topics": result.get("key_topics"),
            }

        except CancelledError as e:
            logger.error(f"Error processing task {task_id}: {e}")
            await self._persist_terminal_status(
                task_id=task_id,
                status="cancelled",
                progress_message="Cancelled by user",
                error_code=None,
            )
            raise
        except TaskProcessingError as e:
            logger.error(f"Error processing task {task_id}: {e}")
            error_code = "task_error"
            if isinstance(e, DownloadError):
                error_code = "download_error"
            elif isinstance(e, TranscriptionError):
                error_code = "transcription_error"
            elif isinstance(e, AnalysisError):
                error_code = "analysis_error"
            # RenderError maps to task_error; any other typed pipeline failure
            # (or an unclassified TaskProcessingError) also persists task_error.

            await self._persist_terminal_status(
                task_id=task_id,
                status="error",
                progress_message=str(e),
                error_code=error_code,
            )
            raise
        except Exception as e:
            # Unknown exception: no message-text guessing. The source layers
            # are responsible for raising typed errors; anything reaching here
            # is genuinely unclassified and persists as task_error.
            logger.error(f"Error processing task {task_id}: {e}")
            await self._persist_terminal_status(
                task_id=task_id,
                status="error",
                progress_message=str(e),
                error_code="task_error",
            )
            raise

    async def _prepare_stage(
        self,
        *,
        task_id: str,
        url: str,
        source_type: str,
        processing_mode: str,
        include_broll: bool,
        sound_effects_count: int,
    ) -> Dict[str, Any]:
        """Prepare inputs for the pipeline: normalize the sfx count, build the
        transcript cache key, and read the cache entry.

        Output contract: returns ``sound_effects_count`` (clamped 0-5),
        ``cache_key``, ``cache_hit``, ``cached_transcript``, and
        ``cached_analysis_json``. Pure read of the cache; no status writes.
        """
        sound_effects_count = clamp_sound_effects_count(sound_effects_count)
        cache_key = self._build_cache_key(
            url, source_type, processing_mode, include_broll, sound_effects_count
        )
        cache_entry = await self.cache_repo.get_cache(self.db, cache_key)
        cached_transcript = (
            cache_entry.get("transcript_text") if cache_entry else None
        )
        cached_analysis_json = (
            cache_entry.get("analysis_json") if cache_entry else None
        )
        cache_hit = bool(cached_transcript and cached_analysis_json)
        return {
            "sound_effects_count": sound_effects_count,
            "cache_key": cache_key,
            "cache_hit": cache_hit,
            "cached_transcript": cached_transcript,
            "cached_analysis_json": cached_analysis_json,
        }

    async def _pipeline_stage(
        self,
        *,
        url: str,
        source_type: str,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        processing_mode: str,
        output_format: str,
        add_subtitles: bool,
        include_broll: bool,
        sound_effects_count: int,
        cached_transcript: Optional[str],
        cached_analysis_json: Optional[str],
        progress_callback: Optional[Callable],
        should_cancel: Optional[Callable],
    ) -> Dict[str, Any]:
        """Run the download/transcribe/analyze/segment pipeline.

        Output contract: returns the ``process_video_complete`` result dict.
        Error contract: every failure surfaces as a typed error - DownloadError,
        TranscriptionError, AnalysisError, or CancelledError - never as a raw
        generic exception; the video service wraps at the source.
        """
        return await self.video_service.process_video_complete(
            url=url,
            source_type=source_type,
            task_id=task_id,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            caption_template=caption_template,
            processing_mode=processing_mode,
            output_format=output_format,
            add_subtitles=add_subtitles,
            include_broll=include_broll,
            sound_effects_count=sound_effects_count,
            cached_transcript=cached_transcript,
            cached_analysis_json=cached_analysis_json,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    async def _render_stage(
        self,
        *,
        task_id: str,
        result: Dict[str, Any],
        segments_to_render: List[Dict[str, Any]],
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        output_format: str,
        add_subtitles: bool,
        normalized_cleanup_settings: Dict[str, Any],
        hook_persist: bool,
        watermark: Optional[str],
        watermark_persist: bool,
        sound_effects_count: int,
        should_cancel: Optional[Callable],
        update_progress: Callable,
        clip_ready_callback: Optional[Callable],
    ) -> tuple[List[str], float]:
        """Render clips one at a time in the P3 order: render -> create_clip
        commit -> per-clip checkpoint CAS -> SSE publish.

        Output contract: returns ``(clip_ids, render_seconds)``. A task whose
        every segment render failed raises RenderError (zero clips); the per-clip
        cancel check raises CancelledError. The checkpoint CAS rejection is
        absorbed (logged) exactly as before.
        """
        video_path = Path(result["video_path"])
        total_clips = len(segments_to_render)
        clips_output_dir = Path(self.config.temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        clip_ids = []
        render_start = perf_counter()

        for i, segment in enumerate(segments_to_render):
            # Check cancellation
            if should_cancel and await should_cancel():
                raise CancelledError("Task cancelled")

            # Real-time tick (Redis only): 70-95% spread across clips
            clip_progress = 70 + int(
                ((i + 1) / total_clips) * 25
            ) if total_clips > 0 else 95
            await update_progress(
                clip_progress,
                f"Creating clip {i + 1}/{total_clips}...",
            )

            # Render single clip in thread pool
            clip_info = await self.video_service.create_single_clip(
                video_path,
                segment,
                i,
                clips_output_dir,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
                normalized_cleanup_settings,
                hook_persist=hook_persist,
                watermark=watermark,
                watermark_persist=watermark_persist,
                task_id=task_id,
                sound_effects_count=sound_effects_count,
            )
            if clip_info is None:
                continue  # Skip failed clip

            # Save to DB immediately (with clip marketing metadata)
            from ..clip_metadata import CLIP_METADATA_VERSION

            clip_id = await self.clip_repo.create_clip(
                self.db,
                task_id=task_id,
                filename=clip_info["filename"],
                file_path=clip_info["path"],
                start_time=clip_info["start_time"],
                end_time=clip_info["end_time"],
                duration=clip_info["duration"],
                text=clip_info.get("text", ""),
                relevance_score=clip_info.get("relevance_score", 0.0),
                reasoning=clip_info.get("reasoning", ""),
                clip_order=i + 1,
                virality_score=clip_info.get("virality_score", 0),
                hook_score=clip_info.get("hook_score", 0),
                engagement_score=clip_info.get("engagement_score", 0),
                value_score=clip_info.get("value_score", 0),
                shareability_score=clip_info.get("shareability_score", 0),
                hook_type=clip_info.get("hook_type"),
                hook_title=clip_info.get("hook_title"),
                description=segment.get("description") or clip_info.get("description"),
                hashtags=segment.get("hashtags") or clip_info.get("hashtags"),
                metadata_status=segment.get("metadata_status") or "pending",
                metadata_version=segment.get("metadata_version") or CLIP_METADATA_VERSION,
                metadata_prompt_version=segment.get("metadata_prompt_version") or CLIP_METADATA_VERSION,
            )
            # ClipRepository.create_clip owns its commit (it mirrors
            # TaskRepository.create_task); no second commit here so the
            # caller never commits across the repository's boundary.
            clip_ids.append(clip_id)

            # Checkpoint: persist progress only after the clip row is
            # committed, so the DB progress column never leads the clip
            # list. The CAS guard rejects the write if the task left
            # processing concurrently (cancelled/errored); the loop's
            # should_cancel check and the terminal CAS absorb that outcome.
            checkpointed = await self.task_repo.update_task_status(
                self.db,
                task_id,
                "processing",
                expected_statuses=["processing"],
                progress=clip_progress,
                progress_message=(
                    f"Creating clip {i + 1}/{total_clips}..."
                ),
            )
            if not checkpointed:
                logger.warning(
                    "Per-clip checkpoint rejected for task %s: "
                    "no longer processing",
                    task_id,
                )

            # Notify frontend via SSE
            if clip_ready_callback:
                clip_record = await self.clip_repo.get_clip_by_id(
                    self.db, clip_id
                )
                if clip_record:
                    await clip_ready_callback(i, total_clips, clip_record)

        render_seconds = round(perf_counter() - render_start, 3)

        if not clip_ids:
            raise RenderError(
                "Clip rendering failed for all segments; no clips were produced"
            )

        return clip_ids, render_seconds

    async def _notify_stage(
        self,
        *,
        task_id: str,
        clip_ids: List[str],
        stage_timings: Dict[str, float],
        progress_callback: Optional[Callable],
    ) -> None:
        """Finish the task: completion CAS, final progress tick, terminal
        runtime metadata, and the completion email.

        Contract: the completed CAS must win (a rejected CAS means the task
        left processing concurrently and raises CancelledError so no
        completion notification is emitted). Email failure is logged, never
        propagated.
        """
        # Mark as completed. If the CAS is rejected the task left
        # processing (cancelled or errored by another writer); treat the
        # run as cancelled so no completion notification is emitted and
        # the terminal status already on the row is never overwritten.
        completed = await self.task_repo.update_task_status(
            self.db,
            task_id,
            "completed",
            expected_statuses=["processing"],
            progress=100,
            progress_message="Complete!",
        )
        if not completed:
            logger.warning(
                "Completion CAS rejected for task %s: status changed from processing",
                task_id,
            )
            raise CancelledError("Task was cancelled while processing")

        if progress_callback:
            await progress_callback(100, "Complete!", "completed")

        await self.task_repo.update_task_runtime_metadata(
            self.db,
            task_id,
            completed_at=datetime.utcnow(),
            stage_timings_json=json.dumps(stage_timings),
            error_code="",
        )
        await self._send_completion_notification_if_needed(
            task_id=task_id,
            clips_count=len(clip_ids),
        )

    async def _persist_terminal_status(
        self,
        *,
        task_id: str,
        status: str,
        progress_message: str,
        error_code: str | None,
    ) -> None:
        """Persist the terminal status transition without leaking a transaction.

        Runs inside process_task's error handlers. If the pipeline failure
        aborted the session's transaction (a DB error was the original
        exception), the handler's own writes would fail with
        PendingRollbackError and mask that original error; roll the aborted
        transaction back first so the status write runs in a clean
        transaction. A failure while persisting the status is swallowed only
        after another rollback, so the original pipeline exception is never
        masked and no transaction is left open on the session.
        """
        try:
            # Always roll the current transaction back before writing the
            # terminal status: after a DB-level pipeline failure the session
            # is aborted and the write would fail with PendingRollbackError,
            # masking the original error. Rollback is a no-op on a clean
            # session, so this cannot discard committed work.
            await self.db.rollback()
            updated = await self.task_repo.update_task_status(
                self.db,
                task_id,
                status,
                expected_statuses=["queued", "processing"],
                progress=0,
                progress_message=progress_message,
            )
            if not updated:
                logger.warning(
                    "Terminal status %s rejected for task %s: "
                    "task is not in queued/processing state",
                    status,
                    task_id,
                )
                return
            if error_code is not None:
                await self.task_repo.update_task_runtime_metadata(
                    self.db,
                    task_id,
                    completed_at=datetime.utcnow(),
                    error_code=error_code,
                )
        except Exception:
            logger.exception(
                "Failed to persist terminal status %s for task %s; rolling back",
                status,
                task_id,
            )
            try:
                await self.db.rollback()
            except Exception:
                pass

    async def _send_completion_notification_if_needed(
        self, *, task_id: str, clips_count: int
    ) -> None:
        context = await self.task_repo.get_task_notification_context(self.db, task_id)
        if not context:
            logger.warning("Task %s missing notification context; skipping email", task_id)
            return

        if not context.get("notify_on_completion"):
            return

        if context.get("completion_notification_sent_at"):
            logger.info(
                "Completion notification already sent for task %s; skipping", task_id
            )
            return

        user_email = context.get("user_email")
        if not user_email:
            logger.warning(
                "Task %s has notify_on_completion enabled but user email is missing",
                task_id,
            )
            return

        email_service = TaskCompletionEmailService(self.config)
        if not email_service.is_configured:
            logger.warning(
                "Skipping completion notification for task %s because Amazon SES is not configured",
                task_id,
            )
            return

        try:
            await email_service.send_task_completed_email(
                recipient=TaskCompletionRecipient(
                    email=user_email,
                    name=context.get("user_name"),
                    first_name=context.get("user_first_name"),
                ),
                task_id=task_id,
                source_title=context.get("source_title"),
                clips_count=clips_count,
            )
            stamped = await self.task_repo.mark_completion_notification_sent(
                self.db, task_id
            )
            if not stamped:
                logger.info(
                    "Completion notification stamp already existed for task %s",
                    task_id,
                )
        except Exception:
            logger.exception(
                "Failed to send completion notification for task %s",
                task_id,
            )

    async def get_task_with_clips(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task details with all clips."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)

        if not task:
            return None

        # Get clips
        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        task["clips"] = [
            {key: value for key, value in clip.items() if key != "file_path"}
            for clip in clips
        ]
        task["clips_count"] = len(clips)
        task.update(await self._load_task_render_settings(task))
        task["sfx_attribution"] = []
        task["sfx_degraded"] = False
        for clip_record, clip in zip(clips, task["clips"]):
            sidecar = Path(clip_record["file_path"]).with_suffix(".sfx.json")
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, dict):
                clip["sfx"] = payload.get("placements", [])
                task["sfx_degraded"] = task["sfx_degraded"] or bool(payload.get("degraded"))
        try:
            from ..sound_effect_cache import read_attribution_manifest
            task["sfx_attribution"] = [
                {key: value for key, value in asset.__dict__.items() if key != "local_mp3_path"}
                for asset in read_attribution_manifest(task_id)
            ]
        except Exception:
            pass

        return task

    async def get_user_tasks(
        self, user_id: str, limit: int = 50
    ) -> list[Dict[str, Any]]:
        """Get all tasks for a user."""
        return await self.task_repo.get_user_tasks(self.db, user_id, limit)

    async def delete_task(self, task_id: str) -> None:
        """Delete a task and all its associated clips."""
        # Delete all clips for this task
        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        # Delete the task
        await self.task_repo.delete_task(self.db, task_id)

        logger.info(f"Deleted task {task_id} and all associated clips")

    async def _attach_fallback_broll_suggestions(
        self,
        segments: List[Dict[str, Any]],
        task_id: str,
        output_format: str,
    ) -> None:
        """Fetch stock footage from clip-text keywords when regenerating stored segments.

        Regeneration reuses existing segment boundaries without re-running the AI analysis,
        so B-roll opportunities are derived from the stored clip text instead. Suggestions
        keep absolute source-video timestamps; the render step maps them into clip-local time.
        """
        runtime_config = get_config()
        if not runtime_config.pexels_api_key:
            logger.warning(
                "include_broll is on but PEXELS_API_KEY is not configured; skipping B-roll"
            )
            return

        from ..broll import (
            build_fallback_broll_opportunities,
            fetch_broll_for_opportunities,
        )

        opportunities = []
        segment_bounds = []
        for segment in segments:
            try:
                start_seconds = parse_timestamp_to_seconds(segment["start_time"])
                end_seconds = parse_timestamp_to_seconds(segment["end_time"])
            except (KeyError, ValueError, IndexError):
                continue
            segment_bounds.append((segment, start_seconds, end_seconds))
            opportunities.extend(
                await build_fallback_broll_opportunities(
                    segment.get("text") or "",
                    start_seconds,
                    end_seconds,
                )
            )

        if not opportunities:
            return

        broll_dir = Path(runtime_config.temp_dir) / "broll" / task_id
        orientation = (
            "portrait" if output_format in ("vertical", "original") else "landscape"
        )
        suggestions = await fetch_broll_for_opportunities(
            opportunities, broll_dir, orientation=orientation
        )

        for suggestion in suggestions:
            for segment, start_seconds, end_seconds in segment_bounds:
                if start_seconds <= suggestion.timestamp <= end_seconds:
                    segment.setdefault("broll_suggestions", []).append(
                        suggestion.model_dump()
                    )
                    break

        logger.info(
            f"Attached fallback B-roll suggestions to "
            f"{sum(1 for s in segments if s.get('broll_suggestions'))} segments"
        )

    async def update_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
        sound_effects_count: int = 0,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        output_format: str = "vertical",
        add_subtitles: bool = True,
        hook_persist: bool = False,
        watermark: Optional[str] = None,
        watermark_persist: bool = False,
    ) -> Dict[str, Any]:
        """Update task-level settings and optionally regenerate all clips."""
        await self.task_repo.update_task_settings(
            self.db,
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            sound_effects_count=sound_effects_count,
            output_format=output_format,
            add_subtitles=add_subtitles,
            hook_persist=hook_persist,
            watermark=watermark,
            watermark_persist=watermark_persist,
            cleanup_settings_json=cleanup_settings,
        )

        if apply_to_existing:
            await self.regenerate_all_clips_for_task(
                task_id,
                font_family,
                font_size,
                font_color,
                caption_template,
                cleanup_settings=cleanup_settings,
                include_broll=include_broll,
                sound_effects_count=sound_effects_count,
            )

        return await self.get_task_with_clips(task_id) or {}

    async def regenerate_all_clips_for_task(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        include_broll: bool = False,
        sound_effects_count: int = 0,
    ) -> None:
        """Regenerate all clips in a task using existing segment boundaries."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        metadata = await self._load_task_render_settings(task)
        output_format = metadata.get("output_format", "vertical")
        persisted_sound_effects_count = task.get("sound_effects_count")
        if persisted_sound_effects_count is None:
            persisted_sound_effects_count = sound_effects_count
        sound_effects_count = max(
            0, min(5, int(persisted_sound_effects_count))
        )
        add_subtitles = metadata.get("add_subtitles", True)
        hook_persist = metadata.get("hook_persist", False)
        watermark = metadata.get("watermark")
        watermark_persist = metadata.get("watermark_persist", False)
        cleanup_payload = cleanup_settings or {
            "cut_long_pauses": metadata.get("cut_long_pauses"),
            "pause_threshold_ms": metadata.get("pause_threshold_ms"),
            "remove_filler_words": metadata.get("remove_filler_words"),
            "filtered_words": metadata.get("filtered_words"),
        }
        normalized_cleanup_settings = normalize_clip_cleanup_settings(
            cleanup_payload.get("cut_long_pauses"),
            cleanup_payload.get("pause_threshold_ms"),
            cleanup_payload.get("remove_filler_words"),
            cleanup_payload.get("filtered_words"),
        )
        existing_cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
        )
        should_recompute_cleanup = (
            cleanup_settings is not None
            and normalized_cleanup_settings != existing_cleanup_settings
        )

        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot regenerate clips")

        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        if not clips:
            return

        video_path: Path
        if source_type == "youtube":
            downloaded = await self.video_service.download_video(source_url)
            if not downloaded:
                raise ValueError("Failed to download source video for regeneration")
            video_path = Path(downloaded)
        else:
            video_path = self.video_service.resolve_local_video_path(source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        segments = []
        for clip in clips:
            source_ranges = self._get_clip_source_ranges(clip)
            bounds = source_range_bounds(source_ranges)
            if bounds:
                start_time = self._seconds_to_mmss(bounds[0])
                end_time = self._seconds_to_mmss(bounds[1])
            else:
                start_time = clip["start_time"]
                end_time = clip["end_time"]

            segments.append(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    **(
                        {"source_ranges": source_ranges}
                        if should_recompute_cleanup
                        else {"keep_ranges": source_ranges}
                    ),
                    "text": clip.get("text") or "",
                    "relevance_score": clip.get("relevance_score", 0.5),
                    "reasoning": clip.get("reasoning")
                    or "Regenerated with updated settings",
                    "virality_score": clip.get("virality_score", 0),
                    "hook_score": clip.get("hook_score", 0),
                    "engagement_score": clip.get("engagement_score", 0),
                    "value_score": clip.get("value_score", 0),
                    "shareability_score": clip.get("shareability_score", 0),
                    "hook_type": clip.get("hook_type"),
                    "hook_title": clip.get("hook_title"),
                    "sfx_opportunities": [],
                    **(
                        {"hook_selection": {
                            "hook_start_time": self._seconds_to_mmss(manifest["hook_range"][0]),
                            "hook_end_time": self._seconds_to_mmss(manifest["hook_range"][1]),
                            "transcript_evidence": "stored clip hook",
                            "reasoning": "Reused persisted hook source range",
                            "hook_score": clip.get("hook_score", 0),
                        }}
                        if (manifest := load_clip_source_manifest(Path(clip["file_path"])))
                        and manifest.get("hook_range")
                        else {}
                    ),
                }
            )

        if include_broll:
            await self._attach_fallback_broll_suggestions(
                segments, task_id, output_format
            )

        clips_info = await self.video_service.create_video_clips(
            video_path,
            segments,
            font_family,
            font_size,
            font_color,
            caption_template,
            output_format,
            add_subtitles,
            normalized_cleanup_settings,
            hook_persist=hook_persist,
            task_id=task_id,
            sound_effects_count=sound_effects_count,
            watermark=watermark,
            watermark_persist=watermark_persist,
        )

        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        for i, clip_info in enumerate(clips_info):
            await self.clip_repo.create_clip(
                self.db,
                task_id=task_id,
                filename=clip_info["filename"],
                file_path=clip_info["path"],
                start_time=clip_info["start_time"],
                end_time=clip_info["end_time"],
                duration=clip_info["duration"],
                text=clip_info.get("text") or "",
                relevance_score=clip_info.get("relevance_score", 0.5),
                reasoning=clip_info.get("reasoning")
                or "Regenerated with updated settings",
                clip_order=i + 1,
                virality_score=clip_info.get("virality_score", 0),
                hook_score=clip_info.get("hook_score", 0),
                engagement_score=clip_info.get("engagement_score", 0),
                value_score=clip_info.get("value_score", 0),
                shareability_score=clip_info.get("shareability_score", 0),
                hook_type=clip_info.get("hook_type"),
                hook_title=clip_info.get("hook_title"),
            )

    async def trim_clip(
        self,
        task_id: str,
        clip_id: str,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        return await self.clip_render.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )

    async def split_clip(
        self, task_id: str, clip_id: str, split_time: float
    ) -> Dict[str, Any]:
        return await self.clip_render.split_clip(task_id, clip_id, split_time)

    async def merge_clips(
        self,
        task_id: str,
        clip_ids: list[str],
        transition: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Merge multiple clips into one, optionally rendering inter-clip transitions."""
        return await self.clip_render.merge_clips(task_id, clip_ids, transition)

    async def update_clip_captions(
        self,
        task_id: str,
        clip_id: str,
        caption_text: str,
        position: str,
        highlight_words: list[str],
    ) -> Dict[str, Any]:
        return await self.clip_render.update_clip_captions(
            task_id, clip_id, caption_text, position, highlight_words
        )

    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Return aggregate processing performance metrics."""
        return await self.task_repo.get_performance_metrics(self.db)

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

    async def _load_task_render_settings(self, task: Dict[str, Any]) -> Dict[str, Any]:
        # Delegation keeps the historical method name stable for callers; the
        # logic lives in TaskMetadataService.
        return await self.metadata.load_source_settings(task)
