"""
Worker tasks - background jobs processed by arq workers.
"""

import logging
from typing import Dict, Any, Optional
import json

from arq import cron

from ..observability import configure_logging, set_trace_id

configure_logging()

logger = logging.getLogger(__name__)


async def sweep_stale_queued_tasks(ctx: Dict[str, Any]) -> int:
    """Recovery sweep: mark queued tasks that outlived the timeout as error.

    GET /tasks/{id} must stay read-only, so the stale-queued transition lives
    here instead of on the read path. Runs on a cron schedule; each task row
    is committed individually via update_task_status.
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..repositories.task_repository import TaskRepository
    from ..services.task_service import QUEUED_TASK_TIMEOUT_MESSAGE, TaskService

    async with AsyncSessionLocal() as db:
        await load_runtime_settings_cache(db)
        task_service = TaskService(db)
        stale_count = 0
        for task in await TaskRepository.get_queued_tasks(db):
            if task_service._is_stale_queued_task(task):
                updated = await task_service.task_repo.update_task_status(
                    db,
                    task["id"],
                    "error",
                    expected_statuses=["queued"],
                    progress=0,
                    progress_message=QUEUED_TASK_TIMEOUT_MESSAGE,
                )
                if updated:
                    stale_count += 1
                else:
                    logger.warning(
                        "Sweep CAS rejected for task %s: no longer queued; skipping",
                        task["id"],
                    )
        if stale_count:
            logger.warning("Marked %d stale queued task(s) as error", stale_count)
        return stale_count


async def reconcile_orphaned_clip_files(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Reclaim rendered clip files that no generated_clips row references.

    Crash leftovers (render killed between file write and DB commit, or before
    the superseded file was dropped) pile up in the clips temp dir. Runs on a
    cron schedule; rows are read once and files are removed per-path.
    """
    from ..database import AsyncSessionLocal
    from ..clip_cleanup import reconcile_orphaned_clip_files as reconcile

    async with AsyncSessionLocal() as db:
        summary = await reconcile(db)
        if summary["removed"]:
            logger.warning(
                "Orphan clip sweep: scanned=%d referenced=%d removed=%d failed=%d",
                summary["scanned"],
                summary["referenced"],
                summary["removed"],
                summary["failed"],
            )
        return summary


async def process_video_task(
    ctx: Dict[str, Any],
    task_id: str,
    url: str,
    source_type: str,
    user_id: str,
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
    watermark: Optional[str] = None,
    watermark_persist: bool = False,
    cleanup_settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Background worker task to process a video.

    Args:
        ctx: arq context (provides Redis connection and other utilities)
        task_id: Task ID to update
        url: Video URL or file path
        source_type: "youtube" or "upload"
        user_id: User ID who created the task
        font_family: Font family for subtitles
        font_size: Font size for subtitles
        font_color: Font color for subtitles

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.progress import ProgressTracker

    set_trace_id(f"task-{task_id}")
    logger.info(f"Worker processing task {task_id}")

    try:
        sound_effects_count = max(0, min(5, int(sound_effects_count)))
    except (TypeError, ValueError):
        sound_effects_count = 0

    # Create progress tracker
    progress = ProgressTracker(ctx["redis"], task_id)

    async with AsyncSessionLocal() as db:
        await load_runtime_settings_cache(db)
        task_service = TaskService(db)

        # The task row is authoritative. This also repairs jobs serialized with
        # an old/default count before the persisted setting was added.
        task_repo = getattr(task_service, "task_repo", None)
        persisted_task = (
            await task_repo.get_task_by_id(db, task_id)
            if task_repo is not None
            else None
        )
        if persisted_task and persisted_task.get("sound_effects_count") is not None:
            try:
                sound_effects_count = max(
                    0, min(5, int(persisted_task["sound_effects_count"]))
                )
            except (TypeError, ValueError):
                sound_effects_count = 0

        try:
            # Progress callback
            async def update_progress(
                percent: int, message: str, status: str = "processing"
            ):
                await progress.update(percent, message, status)
                logger.info(f"Task {task_id}: {percent}% - {message}")

            async def should_cancel() -> bool:
                cancelled = await ctx["redis"].get(f"task_cancel:{task_id}")
                return bool(cancelled)

            async def clip_ready_callback(
                clip_index: int, total_clips: int, clip_data: dict
            ):
                await progress.clip_ready(clip_index, total_clips, clip_data)

            # Process the video
            result = await task_service.process_task(
                task_id=task_id,
                url=url,
                source_type=source_type,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                hook_persist=hook_persist,
                include_broll=include_broll,
                sound_effects_count=sound_effects_count,
                progress_callback=update_progress,
                should_cancel=should_cancel,
                clip_ready_callback=clip_ready_callback,
                watermark=watermark,
                watermark_persist=watermark_persist,
                cleanup_settings=cleanup_settings,
            )

            logger.info(f"Task {task_id} completed successfully")
            return result

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            # No session may return to the pool with an open transaction:
            # roll back whatever the failure left behind (no-op when clean).
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                job_try = int(ctx.get("job_try", 1))
                max_tries = int(getattr(WorkerSettings, "max_tries", 3))
                if job_try >= max_tries:
                    payload = {
                        "task_id": task_id,
                        "error": str(e),
                        "tries": job_try,
                    }
                    await ctx["redis"].set(
                        f"dead_letter:{task_id}", json.dumps(payload)
                    )
                    await ctx["redis"].sadd("tasks:dead_letter", task_id)
                    await progress.error("Task failed permanently after retries")
            except Exception:
                logger.exception("Failed to persist dead-letter payload")
            # Error will be caught by arq and task status will be updated
            raise

# Worker configuration for arq
class WorkerSettings:
    """Configuration for arq worker."""

    from ..config import Config
    from arq.connections import RedisSettings

    config = Config()

    # Functions to run
    functions = [process_video_task]
    queue_name = "supoclip_tasks"

    # Redis settings from environment
    redis_settings = RedisSettings(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0
    )

    # Retry settings
    max_tries = 3  # Retry failed jobs up to 3 times
    job_timeout = 10800  # 3 hour timeout for video processing

    # Worker pool settings
    max_jobs = 4  # Process up to 4 jobs simultaneously
    cron_jobs = [
        cron(sweep_stale_queued_tasks, minute=0),
        cron(reconcile_orphaned_clip_files, minute=0),
    ]
