"""
Task API routes using refactored architecture.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from pathlib import Path
import json
import logging
from typing import Dict, Any, Optional
import re
import secrets

from ...database import get_db
from ...database import AsyncSessionLocal
from ...services.task_service import TaskService
from ...services.billing_service import BillingService, BillingLimitExceeded
from ...errors import DuplicateTaskError, InvalidSourceError
from ...auth_headers import resolve_authenticated_user_id
from ...workers.job_queue import JobQueue
from ...workers.progress import ProgressTracker
from ...config import get_config
from ...font_registry import is_font_accessible
from ...clip_cleanup import normalize_clip_cleanup_settings
from ...task_validation import (
    clamp_sound_effects_count,
    normalize_boolean,
    normalize_font_color,
    normalize_font_family,
    normalize_font_size,
    normalize_output_format,
    normalize_processing_mode,
)
from ...admin_auth import require_admin_user
from ...infra.redis_client import get_redis_client
from ...clip_editor import export_with_preset, EXPORT_PRESETS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


def build_clip_download_filename(clip: Dict[str, Any], suffix: str = "") -> str:
    hook_title = clip.get("hook_title")
    fallback_title = f"Clip {clip.get('clip_order', 0)}"
    title = hook_title if isinstance(hook_title, str) and hook_title.strip() else fallback_title

    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", title)
    title = re.sub(r'[<>:"/\\|?*]', "", title)
    title = re.sub(r"\.{2,}", ".", title).strip().rstrip(". ")
    if not title:
        title = fallback_title

    safe_suffix = re.sub(r"\s+", " ", suffix)
    safe_suffix = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", safe_suffix)
    safe_suffix = re.sub(r'[<>:"/\\|?*]', "", safe_suffix)
    safe_suffix = re.sub(r"\.{2,}", ".", safe_suffix).rstrip(". ")

    return f"{title}{safe_suffix}.mp4"


# Compatibility aliases: the normalization logic moved to task_validation; the
# legacy private names stay resolvable here for existing tests that import them
# from this module. The route code itself uses the public task_validation names.
_normalize_font_size = normalize_font_size
_normalize_font_color = normalize_font_color
_normalize_font_family = normalize_font_family


async def _get_user_id_from_headers(request: Request, db: AsyncSession) -> str:
    """Resolve the authenticated user ID from an API key or signed frontend headers."""
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


async def _require_task_owner(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Ensure authenticated user owns the task."""
    user_id = await _get_user_id_from_headers(request, db)

    task = await task_service.task_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

    return task


PUBLIC_TASK_FIELDS = {
    "source_title",
    "source_type",
    "status",
    "clips_count",
    "created_at",
    "updated_at",
    "sfx_attribution",
    "sfx_degraded",
}
PUBLIC_CLIP_FIELDS = {
    "id",
    "filename",
    "start_time",
    "end_time",
    "duration",
    "text",
    "relevance_score",
    "reasoning",
    "clip_order",
    "created_at",
    "virality_score",
    "hook_score",
    "engagement_score",
    "value_score",
    "shareability_score",
    "hook_type",
    "hook_title",
    "sfx",
}


def _build_public_task(task: Dict[str, Any], share_token: str) -> Dict[str, Any]:
    """Return only fields intended for anyone holding the share URL."""
    public_task = {key: task.get(key) for key in PUBLIC_TASK_FIELDS}
    public_task["clips"] = []
    for clip in task.get("clips", []):
        public_clip = {key: clip.get(key) for key in PUBLIC_CLIP_FIELDS}
        public_clip["video_url"] = (
            f"/tasks/shared/{share_token}/clips/{clip['id']}/file"
        )
        public_task["clips"].append(public_clip)
    return public_task


@router.get("/")
async def list_tasks(
    request: Request, db: AsyncSession = Depends(get_db), limit: int = 50
):
    """
    Get all tasks for the authenticated user.
    """
    user_id = await _get_user_id_from_headers(request, db)

    try:
        task_service = TaskService(db)
        tasks = await task_service.get_user_tasks(user_id, limit)

        return {"tasks": tasks, "total": len(tasks)}

    except Exception as e:
        logger.error(f"Error retrieving user tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving tasks: {str(e)}")


@router.post("/")
async def create_task(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new task and enqueue it for processing.
    Returns task_id immediately.
    """
    data = await request.json()

    raw_source = data.get("source")
    user_id = await _get_user_id_from_headers(request, db)

# Get font options
    font_options = data.get("font_options", {})
    font_family = normalize_font_family(font_options.get("font_family"))
    font_size = normalize_font_size(font_options.get("font_size"))
    font_color = normalize_font_color(font_options.get("font_color"))
    caption_template = data.get("caption_template", "default")
    include_broll = data.get("include_broll", False)
    sound_effects_count = clamp_sound_effects_count(
        data.get("sound_effects_count", 0)
    )
    runtime_config = get_config()
    processing_mode = normalize_processing_mode(
        data.get("processing_mode", runtime_config.default_processing_mode),
        runtime_config.default_processing_mode,
    )
    output_format = normalize_output_format(data.get("output_format", "vertical"))
    add_subtitles = normalize_boolean(data.get("add_subtitles", True), True)
    hook_persist = normalize_boolean(data.get("hook_persist", False), False)
    watermark = data.get("watermark")
    if not isinstance(watermark, str):
        watermark = None
    watermark_persist = normalize_boolean(
        data.get("watermark_persist", False), False
    )
    cleanup_settings = normalize_clip_cleanup_settings(
        data.get("cut_long_pauses"),
        data.get("pause_threshold_ms"),
        data.get("remove_filler_words"),
        data.get("filtered_words"),
    )
    if not raw_source or not raw_source.get("url"):
        raise HTTPException(status_code=400, detail="Source URL is required")

    try:
        billing_service = BillingService(db)
        await billing_service.assert_can_create_task(user_id)

        task_service = TaskService(db)

        # Reject duplicate submissions: if the same video (YouTube id or
        # uploaded file) is already being processed, block the new task.
        existing_task = await task_service.find_active_task_for_source(
            raw_source["url"]
        )
        if existing_task:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Video ini sedang diproses (task {existing_task['id']} masih berjalan). "
                    "Tunggu hingga selesai sebelum memproses video yang sama lagi."
                ),
            )

        # Create task
        task_id = await task_service.create_task_with_source(
            user_id=user_id,
            url=raw_source["url"],
            title=raw_source.get("title"),
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
            cleanup_settings=cleanup_settings,
        )

        # Get source type for worker
        source_type = task_service.video_service.determine_source_type(
            raw_source["url"]
        )

        # Enqueue job for worker
        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        job_id = await queue_adapter.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id=task_id,
            url=raw_source["url"],
            source_type=source_type,
            user_id=user_id,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            caption_template=caption_template,
            output_format=output_format,
            add_subtitles=add_subtitles,
            hook_persist=hook_persist,
            include_broll=include_broll,
            sound_effects_count=sound_effects_count,
            watermark=watermark,
            watermark_persist=watermark_persist,
            cleanup_settings=cleanup_settings,
        )

        logger.info(f"Task {task_id} created and job {job_id} enqueued")

        return {
            "task_id": task_id,
            "job_id": job_id,
            "message": "Task created and queued for processing",
        }

    except InvalidSourceError:
        # Client supplied a URL that is neither a YouTube link nor an
        # upload:// reference; this is invalid input, not a missing resource.
        raise HTTPException(
            status_code=400,
            detail="Source URL is not a supported video link. Use a YouTube URL or an uploaded video.",
        )
    except DuplicateTaskError as e:
        # The DB unique index rejected a concurrent duplicate submission that
        # slipped past the pre-check above (race window). Same contract as the
        # pre-check: 409 Conflict.
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except BillingLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": "Choose a paid plan to process videos.",
                "billing": e.summary,
            },
        )
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating task: {str(e)}")


@router.get("/billing/summary")
async def get_billing_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Get monetization status and current usage for authenticated user."""
    user_id = await _get_user_id_from_headers(request, db)

    try:
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(user_id)
        return summary
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving billing summary: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving billing summary: {str(e)}",
        )


@router.get("/shared/{share_token}")
async def get_shared_task(share_token: str, db: AsyncSession = Depends(get_db)):
    """Get the read-only generation result associated with an opaque share token."""
    task_service = TaskService(db)
    task_id = await task_service.task_repo.get_shared_task_id(db, share_token)
    if not task_id:
        raise HTTPException(status_code=404, detail="Shared result not found")

    task = await task_service.get_task_with_clips(task_id)
    if not task or task.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Shared result not found")

    return _build_public_task(task, share_token)


@router.get("/shared/{share_token}/clips/{clip_id}/file")
async def get_shared_clip_file(
    share_token: str,
    clip_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve one shared clip only when the bearer share token is enabled."""
    task_service = TaskService(db)
    task_id = await task_service.task_repo.get_shared_task_id(db, share_token)
    if not task_id:
        raise HTTPException(status_code=404, detail="Shared result not found")

    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_path = Path(clip["file_path"])
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        filename=build_clip_download_filename(clip),
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{task_id}")
async def get_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get task details."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return task

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving task: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving task: {str(e)}")


@router.get("/{task_id}/clips")
async def get_task_clips(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get all clips for a task."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return {
            "task_id": task_id,
            "clips": task.get("clips", []),
            "total_clips": len(task.get("clips", [])),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving clips: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving clips: {str(e)}")


@router.post("/{task_id}/share")
async def share_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Create or re-enable a stable, read-only share URL for a completed task."""
    task_service = TaskService(db)
    task = await _require_task_owner(request, task_service, db, task_id)
    if task.get("status") != "completed":
        raise HTTPException(
            status_code=409, detail="Only completed generations can be shared"
        )

    share_token = await task_service.task_repo.enable_sharing(
        db, task_id, secrets.token_urlsafe(32)
    )
    if not share_token:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "share_token": share_token,
        "share_path": f"/share/{share_token}",
    }


@router.delete("/{task_id}/share")
async def unshare_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Disable the public URL while preserving the private generation result."""
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)
    await task_service.task_repo.disable_sharing(db, task_id)
    return {"message": "Share link disabled"}


@router.get("/{task_id}/progress")
async def get_task_progress_sse(
    task_id: str, request: Request, mode: str = "task"
):
    """
    SSE endpoint for real-time progress updates.
    Streams progress updates as Server-Sent Events.

    mode=edit keeps the stream open for completed tasks so clip re-render
    progress (caption edits) can be delivered to the editor page; the default
    task mode closes as soon as the main pipeline finishes.
    """

    async with AsyncSessionLocal() as local_db:
        user_id = await _get_user_id_from_headers(request, local_db)
        task_service = TaskService(local_db)
        task = await task_service.task_repo.get_task_by_id(local_db, task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

    edit_mode = mode == "edit"

    async def event_generator():
        """Generate SSE events for task progress."""
        # Send initial task status
        yield {
            "event": "status",
            "data": json.dumps(
                {
                    "task_id": task_id,
                    "status": task.get("status"),
                    "progress": task.get("progress", 0),
                    "message": task.get("progress_message", ""),
                }
            ),
        }

        # If task is already completed or error, close connection
        if not edit_mode and task.get("status") in ["completed", "error"]:
            yield {"event": "close", "data": json.dumps({"status": task.get("status")})}
            return

        # Connect to Redis for real-time updates
        redis_client = get_redis_client()

        # Subscribe to progress updates
        async for progress_data in ProgressTracker.subscribe_to_progress(
            redis_client, task_id
        ):
            event_type = progress_data.get("event_type", "progress")
            yield {"event": event_type, "data": json.dumps(progress_data)}

            # Close connection if task is done (task mode only)
            if not edit_mode and progress_data.get("status") in ["completed", "error"]:
                yield {
                    "event": "close",
                    "data": json.dumps({"status": progress_data.get("status")}),
                }
                break

    return EventSourceResponse(event_generator())


@router.patch("/{task_id}")
async def update_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task details (title)."""
    try:
        data = await request.json()
        title = data.get("title")

        if not title:
            raise HTTPException(status_code=400, detail="Title is required")

        task_service = TaskService(db)

        task = await _require_task_owner(request, task_service, db, task_id)

        # Update source title
        await task_service.source_repo.update_source_title(db, task["source_id"], title)

        return {"message": "Task updated successfully", "task_id": task_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating task: {str(e)}")


@router.delete("/{task_id}")
async def delete_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a task and all its associated clips."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        # Get task to verify ownership
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this task"
            )

        # Delete clips and task
        await task_service.delete_task(task_id)

        return {"message": "Task deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")


@router.delete("/{task_id}/clips/{clip_id}")
async def delete_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a specific clip."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        # Verify task ownership
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this clip"
            )

        # Delete the clip
        await task_service.clip_repo.delete_clip(db, clip_id)

        return {"message": "Clip deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting clip: {str(e)}")


@router.get("/{task_id}/clips/{clip_id}/file")
async def get_clip_file(
    task_id: str,
    clip_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve a clip file after verifying task ownership."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        clip_path = Path(clip["file_path"])
        if not clip_path.exists():
            raise HTTPException(status_code=404, detail="Clip file not found")

        return FileResponse(
            path=str(clip_path),
            media_type="video/mp4",
            filename=build_clip_download_filename(clip),
            content_disposition_type="inline",
            headers={"Cache-Control": "private, no-store"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving clip file: {e}")
        raise HTTPException(status_code=500, detail=f"Error serving clip file: {str(e)}")


@router.patch("/{task_id}/clips/{clip_id}")
async def trim_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Trim clip boundaries and regenerate clip file."""
    try:
        payload = await request.json()
        start_offset = float(payload.get("start_offset", 0))
        end_offset = float(payload.get("end_offset", 0))

        if start_offset < 0 or end_offset < 0:
            raise HTTPException(status_code=400, detail="Offsets must be non-negative")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error trimming clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error trimming clip: {str(e)}")


@router.post("/{task_id}/clips/{clip_id}/split")
async def split_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Split a clip into two clips."""
    try:
        payload = await request.json()
        split_time = float(payload.get("split_time", 0))
        if split_time <= 0:
            raise HTTPException(
                status_code=400, detail="split_time must be greater than zero"
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.split_clip(task_id, clip_id, split_time)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error splitting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error splitting clip: {str(e)}")


@router.post("/{task_id}/clips/merge")
async def merge_clips(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Merge multiple clips into one clip."""
    try:
        payload = await request.json()
        clip_ids = payload.get("clip_ids") or []
        if not isinstance(clip_ids, list):
            raise HTTPException(status_code=400, detail="clip_ids must be an array")
        transition = payload.get("transition")
        if not isinstance(transition, str):
            transition = None

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.merge_clips(
            task_id, clip_ids, transition=transition
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error merging clips: {e}")
        raise HTTPException(status_code=500, detail=f"Error merging clips: {str(e)}")


@router.patch("/{task_id}/clips/{clip_id}/captions")
async def update_clip_captions(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update clip caption text, timing style and highlighted words."""
    try:
        payload = await request.json()
        caption_text = str(payload.get("caption_text", "")).strip()
        position = str(payload.get("position", "bottom"))
        highlight_words = payload.get("highlight_words") or []
        if not isinstance(highlight_words, list):
            raise HTTPException(
                status_code=400, detail="highlight_words must be an array"
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.update_clip_captions(
            task_id,
            clip_id,
            caption_text,
            position,
            [str(word) for word in highlight_words],
        )
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating captions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating captions: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/regenerate")
async def regenerate_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Regenerate a single clip after editing timing values."""
    try:
        payload = await request.json()
        start_offset = float(payload.get("start_offset", 0))
        end_offset = float(payload.get("end_offset", 0))

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )
        return {"clip": updated_clip, "message": "Clip regenerated successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating clip: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error regenerating clip: {str(e)}"
        )


@router.post("/{task_id}/settings")
async def apply_task_settings(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task-level styling settings and optionally apply to all existing clips."""
    try:
        payload = await request.json()
        font_family = normalize_font_family(payload.get("font_family"))
        font_size = normalize_font_size(payload.get("font_size"))
        font_color = normalize_font_color(payload.get("font_color"))
        caption_template = payload.get("caption_template", "default")
        include_broll = bool(payload.get("include_broll", False))
        sound_effects_count = clamp_sound_effects_count(
            payload.get("sound_effects_count", 0)
        )
        apply_to_existing = bool(payload.get("apply_to_existing", False))
        cleanup_settings = normalize_clip_cleanup_settings(
            payload.get("cut_long_pauses"),
            payload.get("pause_threshold_ms"),
            payload.get("remove_filler_words"),
            payload.get("filtered_words"),
        )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task_record = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task_record:
            raise HTTPException(status_code=404, detail="Task not found")
        if "sound_effects_count" not in payload:
            sound_effects_count = int(task_record.get("sound_effects_count") or 0)
        if font_family is not None and not is_font_accessible(
            font_family, task_record["user_id"]
        ):
            raise HTTPException(
                status_code=400, detail="Selected font is not available"
            )
        # Output format / subtitle settings default to the current DB record so
        # a partial payload never silently resets render settings. The invalid
        # value fallback stays the hard-coded "vertical" (historical behavior).
        output_format = normalize_output_format(
            payload.get(
                "output_format", task_record.get("output_format") or "vertical"
            )
        )
        add_subtitles = normalize_boolean(
            payload.get(
                "add_subtitles", task_record.get("add_subtitles", True)
            ),
            task_record.get("add_subtitles", True),
        )
        hook_persist = normalize_boolean(
            payload.get(
                "hook_persist", task_record.get("hook_persist", False)
            ),
            task_record.get("hook_persist", False),
        )
        watermark = payload.get("watermark", task_record.get("watermark"))
        if not isinstance(watermark, str):
            watermark = None
        watermark_persist = normalize_boolean(
            payload.get(
                "watermark_persist", task_record.get("watermark_persist", False)
            ),
            task_record.get("watermark_persist", False),
        )
        task = await task_service.update_task_settings(
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            apply_to_existing,
            cleanup_settings=cleanup_settings,
            sound_effects_count=sound_effects_count,
            output_format=output_format,
            add_subtitles=add_subtitles,
            hook_persist=hook_persist,
watermark=watermark,
            watermark_persist=watermark_persist,
        )
        # Settings are persisted in the tasks row by update_task_settings;
        # there is no separate metadata cache to refresh.
        return {"task": task, "message": "Task settings updated"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task settings: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating task settings: {str(e)}"
        )


@router.get("/{task_id}/clips/{clip_id}/export")
async def export_clip(
    task_id: str,
    clip_id: str,
    request: Request,
    preset: str = "tiktok",
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Export clip with a social platform preset."""
    try:
        preset_name = preset.lower().strip()
        if preset_name not in EXPORT_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid preset. Use one of: {', '.join(EXPORT_PRESETS.keys())}",
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        from pathlib import Path

        runtime_config = get_config()
        output_path = export_with_preset(
            Path(clip["file_path"]),
            Path(runtime_config.temp_dir) / "exports",
            preset_name,
        )

        download_name = build_clip_download_filename(clip, f"_{preset_name}")
        return FileResponse(
            path=str(output_path), media_type="video/mp4", filename=download_name
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error exporting clip: {str(e)}")


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Cancel an active queued or processing task."""
    try:
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        if task.get("status") in ["completed", "error", "cancelled"]:
            return {"message": f"Task already in terminal state: {task.get('status')}"}

        redis_client = get_redis_client()
        await redis_client.setex(f"task_cancel:{task_id}", 3600, "1")

        updated = await task_service.task_repo.update_task_status(
            db,
            task_id,
            "cancelled",
            expected_statuses=["queued", "processing"],
            progress=0,
            progress_message="Cancelled by user",
        )
        if not updated:
            # The task left queued/processing between the read and this write
            # (worker completed/errored it, or another cancel won); never
            # overwrite the terminal status.
            logger.warning(
                "Cancel CAS rejected for task %s: status changed concurrently",
                task_id,
            )
            current = await task_service.task_repo.get_task_by_id(db, task_id)
            return {
                "message": (
                    f"Task already in terminal state: "
                    f"{current.get('status') if current else 'unknown'}"
                )
            }

        return {"message": "Task cancellation requested"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling task: {e}")
        raise HTTPException(status_code=500, detail=f"Error cancelling task: {str(e)}")


@router.get("/metrics/performance")
async def get_performance_metrics(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Get aggregate processing performance metrics by mode."""
    try:
        await require_admin_user(request, db, get_config())
        task_service = TaskService(db)
        return await task_service.get_performance_metrics()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading metrics: {str(e)}")


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Resume a cancelled or errored task by enqueueing a new worker job."""
    try:
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        if task.get("status") not in ["cancelled", "error", "queued"]:
            raise HTTPException(
                status_code=400,
                detail="Only cancelled/error/queued tasks can be resumed",
            )

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        # P4: the tasks row (Schema v2 columns) is the single authority for
        # render settings. The legacy Redis fallback was removed: a missing
        # value marks a legacy row that was never backfilled and must fail
        # loudly instead of silently resuming with defaults.
        output_format = normalize_output_format(task.get("output_format") or "vertical")
        add_subtitles = normalize_boolean(task.get("add_subtitles", True), True)
        hook_persist = normalize_boolean(task.get("hook_persist", False), False)
        watermark = task.get("watermark")
        if not isinstance(watermark, str):
            watermark = None
        watermark_persist = normalize_boolean(
            task.get("watermark_persist", False), False
        )
        cleanup_settings = None
        cleanup_settings_json = task.get("cleanup_settings_json")
        if cleanup_settings_json is not None:
            # asyncpg 0.31 decodes the jsonb column to a dict natively, so the
            # column may arrive either already parsed or as a JSON string.
            parsed_cleanup = cleanup_settings_json
            if isinstance(cleanup_settings_json, str):
                try:
                    parsed_cleanup = json.loads(cleanup_settings_json)
                except json.JSONDecodeError:
                    parsed_cleanup = None
            if isinstance(parsed_cleanup, dict):
                cleanup_settings = normalize_clip_cleanup_settings(
                    parsed_cleanup.get("cut_long_pauses"),
                    parsed_cleanup.get("pause_threshold_ms"),
                    parsed_cleanup.get("remove_filler_words"),
                    parsed_cleanup.get("filtered_words"),
                )

        sound_effects_count = clamp_sound_effects_count(
            task.get("sound_effects_count") or 0
        )

        # Fail loudly on legacy rows whose Schema v2 columns were never
        # backfilled: resuming with defaults would change the user's render
        # settings silently, and the Redis fallback no longer exists.
        if not source_url or not source_type:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Task source information is missing; this legacy task was "
                    "not migrated and cannot be resumed. Please create a new task."
                ),
            )
        if task.get("output_format") is None or task.get("add_subtitles") is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Task render settings are missing; this legacy task was "
                    "not migrated and cannot be resumed. Please create a new task."
                ),
            )
        if cleanup_settings is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Task cleanup settings are missing; this legacy task was "
                    "not migrated and cannot be resumed. Please create a new task."
                ),
            )

        runtime_config = get_config()

        requeued = await task_service.task_repo.update_task_status(
            db,
            task_id,
            "queued",
            expected_statuses=["cancelled", "error", "queued"],
            progress=0,
            progress_message="Re-queued by user",
        )
        if not requeued:
            # The task left cancelled/error/queued between the read and this
            # write (e.g. it completed or a new job took it over); do not
            # enqueue a worker job against a task that is not resumable.
            logger.warning(
                "Resume CAS rejected for task %s: status changed concurrently",
                task_id,
            )
            raise HTTPException(
                status_code=409,
                detail="Task status changed concurrently; it can no longer be resumed",
            )

        # Only clear the cancel flag after the re-queue won, so a concurrent
        # cancel request cannot resurrect the flag for the new job.
        redis_client = get_redis_client()
        await redis_client.delete(f"task_cancel:{task_id}")

        processing_mode = (
            task.get("processing_mode") or runtime_config.default_processing_mode
        )

        job_id = await JobQueue.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id=task_id,
            url=source_url,
            source_type=source_type,
            user_id=task["user_id"],
            font_family=task.get("font_family"),
            font_size=task.get("font_size"),
            font_color=task.get("font_color"),
            caption_template=task.get("caption_template") or "default",
            output_format=output_format,
            add_subtitles=add_subtitles,
            hook_persist=hook_persist,
            include_broll=bool(task.get("include_broll", False)),
            sound_effects_count=sound_effects_count,
            watermark=watermark,
            watermark_persist=watermark_persist,
            cleanup_settings=cleanup_settings,
        )

        return {"message": "Task resumed", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming task: {e}")
        raise HTTPException(status_code=500, detail=f"Error resuming task: {str(e)}")


@router.get("/dead-letter/list")
async def list_dead_letter_tasks(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """List tasks that exhausted retries and landed in dead-letter store."""
    await require_admin_user(request, db, get_config())
    redis_client = get_redis_client()
    ids = await redis_client.smembers("tasks:dead_letter")
    items = []
    safe_ids = list(ids or [])
    for task_id in sorted(safe_ids):
        payload = await redis_client.get(f"dead_letter:{task_id}")
        if payload:
            try:
                items.append(json.loads(payload))
            except json.JSONDecodeError:
                items.append({"task_id": task_id, "raw": payload})

    return {"total": len(items), "tasks": items}
