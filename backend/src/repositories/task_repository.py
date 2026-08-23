"""
Task repository - handles all database operations for tasks.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, Dict, Any, List
from datetime import datetime
import json
import logging

from ..task_validation import clamp_sound_effects_count

logger = logging.getLogger(__name__)


def _serialize_cleanup_settings_json(
    cleanup_settings_json: Optional[Any],
) -> Optional[str]:
    """Coerce cleanup settings into the JSON string bound to the jsonb column.

    asyncpg refuses raw dicts for jsonb columns on text() SQL; the caller may
    hand in either the already-serialized string or a dict.
    """
    if cleanup_settings_json is None or isinstance(cleanup_settings_json, str):
        return cleanup_settings_json
    return json.dumps(cleanup_settings_json)


class TaskRepository:
    """Repository for task-related database operations."""

    @staticmethod
    async def create_task(
        db: AsyncSession,
        user_id: str,
        source_id: str,
        status: str = "processing",
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
        cleanup_settings_json: Optional[Any] = None,
        source_identity: Optional[str] = None,
        commit: bool = True,
    ) -> str:
        """Create a new task and return its ID.

        ``commit=False`` defers the transaction boundary to the caller (the
        task service wraps source + task + billing reservation in one atomic
        commit). Defaults to True so all existing callers keep their own
        commit behavior.
        """
        task_id = str(uuid4())
        # B3 fallback removed: single INSERT, fail-loud on any DB error.
        # B2: task source settings are persisted here (output_format /
        # add_subtitles / cleanup_settings_json), the Redis key is only a cache.
        result = await db.execute(
            text("""
                INSERT INTO tasks (
                    id, user_id, source_id, status, font_family, font_size, font_color,
                    caption_template, include_broll, sound_effects_count, processing_mode,
                    output_format, add_subtitles, hook_persist, watermark, watermark_persist,
                    cleanup_settings_json, source_identity,
                    created_at, updated_at
                )
                VALUES (
                    :task_id, :user_id, :source_id, :status, :font_family, :font_size, :font_color,
                    :caption_template, :include_broll, :sound_effects_count, :processing_mode,
                    :output_format, :add_subtitles, :hook_persist, :watermark, :watermark_persist,
                    CAST(:cleanup_settings_json AS jsonb), :source_identity,
                    NOW(), NOW()
                )
                RETURNING id
            """),
            {
                "task_id": task_id,
                "user_id": user_id,
                "source_id": source_id,
                "status": status,
                "font_family": font_family,
                "font_size": font_size,
                "font_color": font_color,
                "caption_template": caption_template,
                "include_broll": include_broll,
                "sound_effects_count": clamp_sound_effects_count(
                    sound_effects_count
                ),
                "processing_mode": processing_mode,
                "output_format": output_format,
                "add_subtitles": add_subtitles,
                "hook_persist": hook_persist,
                "watermark": watermark,
                "watermark_persist": watermark_persist,
                "cleanup_settings_json": _serialize_cleanup_settings_json(
                    cleanup_settings_json
                ),
                "source_identity": source_identity,
            },
        )
        if commit:
            await db.commit()
        task_id = result.scalar()
        if not task_id:
            raise RuntimeError("Failed to create task: no ID returned")
        logger.info(f"Created task {task_id} for user {user_id}")
        return str(task_id)

    @staticmethod
    async def get_task_by_id(
        db: AsyncSession, task_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get task by ID with source information."""
        # B3 fallback removed: single SELECT with sources.url (NOT NULL in
        # Schema v2), fail-loud on any DB error.
        result = await db.execute(
            text("""
                SELECT t.*, s.title as source_title, s.type as source_type, s.url as source_url
                FROM tasks t
                LEFT JOIN sources s ON t.source_id = s.id
                WHERE t.id = :task_id
            """),
            {"task_id": task_id},
        )
        row = result.fetchone()

        if not row:
            return None

        return {
            "id": row.id,
            "user_id": row.user_id,
            "source_id": row.source_id,
            "source_title": row.source_title,
            "source_type": row.source_type,
            "status": row.status,
            "progress": row.progress,
            "progress_message": row.progress_message,
            "font_family": row.font_family,
            "font_size": row.font_size,
            "font_color": row.font_color,
            "caption_template": row.caption_template,
            "include_broll": row.include_broll,
            "sound_effects_count": row.sound_effects_count or 0,
            "processing_mode": row.processing_mode,
            "output_format": row.output_format,
            "add_subtitles": row.add_subtitles,
            "hook_persist": row.hook_persist,
            "watermark": row.watermark,
            "watermark_persist": row.watermark_persist,
            "cleanup_settings_json": row.cleanup_settings_json,
            "cache_hit": row.cache_hit,
            "error_code": row.error_code,
            "stage_timings_json": row.stage_timings_json,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "completion_notification_sent_at": row.completion_notification_sent_at,
            "source_url": row.source_url,
            "source_identity": row.source_identity,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    async def update_task_runtime_metadata(
        db: AsyncSession,
        task_id: str,
        cache_hit: Optional[bool] = None,
        error_code: Optional[str] = None,
        stage_timings_json: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        params: Dict[str, Any] = {"task_id": task_id}
        set_parts = []

        if cache_hit is not None:
            set_parts.append("cache_hit = :cache_hit")
            params["cache_hit"] = cache_hit

        if error_code is not None:
            set_parts.append("error_code = :error_code")
            params["error_code"] = error_code

        if stage_timings_json is not None:
            set_parts.append("stage_timings_json = :stage_timings_json")
            params["stage_timings_json"] = stage_timings_json

        if started_at is not None:
            set_parts.append("started_at = :started_at")
            params["started_at"] = started_at

        if completed_at is not None:
            set_parts.append("completed_at = :completed_at")
            params["completed_at"] = completed_at

        if not set_parts:
            return

        set_parts.append("updated_at = NOW()")
        query = f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = :task_id"
        await db.execute(text(query), params)
        await db.commit()

    @staticmethod
    async def get_performance_metrics(db: AsyncSession) -> Dict[str, Any]:
        result = await db.execute(
            text(
                """
                SELECT
                    processing_mode,
                    COUNT(*) AS total_tasks,
                    AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) AS avg_seconds,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))) AS p50_seconds,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))) AS p95_seconds,
                    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits
                FROM tasks
                WHERE started_at IS NOT NULL AND completed_at IS NOT NULL
                GROUP BY processing_mode
                ORDER BY processing_mode
                """
            )
        )

        rows = result.fetchall()
        metrics = []
        for row in rows:
            total = int(row.total_tasks or 0)
            cache_hits = int(row.cache_hits or 0)
            metrics.append(
                {
                    "processing_mode": row.processing_mode,
                    "total_tasks": total,
                    "avg_seconds": float(row.avg_seconds or 0),
                    "p50_seconds": float(row.p50_seconds or 0),
                    "p95_seconds": float(row.p95_seconds or 0),
                    "cache_hit_rate": (cache_hits / total) if total else 0,
                }
            )

        return {"modes": metrics}

    @staticmethod
    async def update_task_settings(
        db: AsyncSession,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        sound_effects_count: int = 0,
        output_format: str = "vertical",
        add_subtitles: bool = True,
        hook_persist: bool = False,
        watermark: Optional[str] = None,
        watermark_persist: bool = False,
        cleanup_settings_json: Optional[Any] = None,
    ) -> None:
        """Update task styling settings.

        B2: output_format / add_subtitles / cleanup_settings_json are persisted
        in the same statement so the Redis task_source key is only a cache.
        """
        # B3 fallback removed: single UPDATE with caption_template/include_broll,
        # fail-loud on any DB error.
        await db.execute(
            text(
                """
                UPDATE tasks
                SET font_family = :font_family,
                    font_size = :font_size,
                    font_color = :font_color,
                    caption_template = :caption_template,
                    include_broll = :include_broll,
                    sound_effects_count = :sound_effects_count,
                    output_format = :output_format,
                    add_subtitles = :add_subtitles,
                    hook_persist = :hook_persist,
                    watermark = :watermark,
                    watermark_persist = :watermark_persist,
                    cleanup_settings_json = CAST(:cleanup_settings_json AS jsonb),
                    updated_at = NOW()
                WHERE id = :task_id
                """
            ),
            {
                "task_id": task_id,
                "font_family": font_family,
                "font_size": font_size,
                "font_color": font_color,
                "caption_template": caption_template,
"include_broll": include_broll,
                "sound_effects_count": clamp_sound_effects_count(
                    sound_effects_count
                ),
                "output_format": output_format,
                "add_subtitles": add_subtitles,
                "hook_persist": hook_persist,
                "watermark": watermark,
                "watermark_persist": watermark_persist,
                "cleanup_settings_json": _serialize_cleanup_settings_json(
                    cleanup_settings_json
                ),
            },
        )
        await db.commit()

    @staticmethod
    async def update_task_status(
        db: AsyncSession,
        task_id: str,
        status: str,
        *,
        expected_statuses: List[str],
        progress: Optional[int] = None,
        progress_message: Optional[str] = None,
    ) -> bool:
        """CAS-style status transition: apply only when the current status is
        one of expected_statuses.

        Returns True when exactly one row matched the guard and was updated.
        A False result means the status changed concurrently (or the row is
        gone); the caller must not retry blindly and must not overwrite.
        """
        if not expected_statuses:
            raise ValueError("expected_statuses must be non-empty")

        params: Dict[str, Any] = {"task_id": task_id, "status": status}

        # Build dynamic query based on what's provided
        set_parts = ["status = :status"]

        if progress is not None:
            set_parts.append("progress = :progress")
            params["progress"] = progress

        if progress_message is not None:
            set_parts.append("progress_message = :progress_message")
            params["progress_message"] = progress_message

        set_parts.append("updated_at = NOW()")

        # Literal placeholders instead of `IN :expected`: asyncpg does not
        # expand list binds through text() and the values come from the
        # pinned transition matrix, never from user input.
        status_params = {
            f"expected_{i}": expected for i, expected in enumerate(expected_statuses)
        }
        params.update(status_params)
        status_in = ", ".join(f":expected_{i}" for i in range(len(expected_statuses)))

        query = (
            f"UPDATE tasks SET {', '.join(set_parts)} "
            f"WHERE id = :task_id AND status IN ({status_in})"
        )

        result = await db.execute(text(query), params)
        await db.commit()
        # DML execution yields a CursorResult at runtime; the static Result
        # type hides rowcount, so silence the attr lookup explicitly.
        updated = result.rowcount > 0  # type: ignore[attr-defined]
        if updated:
            logger.info(
                f"Updated task {task_id} status to {status}"
                + (f" (progress: {progress}%)" if progress else "")
            )
        return updated

    @staticmethod
    async def get_user_tasks(
        db: AsyncSession, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get all tasks for a user."""
        result = await db.execute(
            text("""
                SELECT t.*, s.title as source_title, s.type as source_type,
                       s.url as source_url,
                       (SELECT COUNT(*) FROM generated_clips WHERE task_id = t.id) as clips_count
                FROM tasks t
                LEFT JOIN sources s ON t.source_id = s.id
                WHERE t.user_id = :user_id
                ORDER BY t.created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        )

        tasks = []
        for row in result.fetchall():
            tasks.append(
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "source_id": row.source_id,
                    "source_title": row.source_title,
                    "source_type": row.source_type,
                    "source_url": getattr(row, "source_url", None),
                    "status": row.status,
                    "processing_mode": getattr(row, "processing_mode", "fast"),
                    "completion_notification_sent_at": getattr(
                        row, "completion_notification_sent_at", None
                    ),
                    "clips_count": row.clips_count,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )

        return tasks

    @staticmethod
    async def get_active_tasks_with_sources(
        db: AsyncSession,
    ) -> List[Dict[str, Any]]:
        """All tasks still being processed, with their source URL.

        Used to reject duplicate submissions: the same video (YouTube id or
        uploaded file) must not run through the pipeline twice at once.
        """
        result = await db.execute(
            text("""
                SELECT t.id, t.user_id, COALESCE(s.url, '') AS source_url
                FROM tasks t
                LEFT JOIN sources s ON t.source_id = s.id
                WHERE t.status NOT IN ('completed', 'error', 'cancelled', 'deleted')
                ORDER BY t.created_at ASC
            """)
        )
        return [
            {"id": row.id, "user_id": row.user_id, "source_url": row.source_url or ""}
            for row in result.fetchall()
        ]

    @staticmethod
    async def get_queued_tasks(db: AsyncSession) -> List[Dict[str, Any]]:
        """All tasks waiting in the queue, with timestamps for staleness checks.

        Used by the worker recovery sweep to mark queued tasks that outlived
        the timeout as error.
        """
        result = await db.execute(
            text("""
                SELECT t.id, t.status, t.created_at, t.updated_at
                FROM tasks t
                WHERE t.status = 'queued'
                ORDER BY t.created_at ASC
            """)
        )
        return [
            {
                "id": row.id,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def get_processing_tasks(db: AsyncSession) -> List[Dict[str, Any]]:
        """All tasks stuck in processing, with timestamps for staleness checks.

        Used by the worker recovery sweep to mark processing tasks whose worker
        died mid-job (no heartbeat update within the window) as error, so they
        never spin forever in the UI.
        """
        result = await db.execute(
            text("""
                SELECT t.id, t.status, t.created_at, t.updated_at
                FROM tasks t
                WHERE t.status = 'processing'
                ORDER BY t.created_at ASC
            """)
        )
        return [
            {
                "id": row.id,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def enable_sharing(
        db: AsyncSession, task_id: str, share_token: str
    ) -> Optional[str]:
        """Enable public sharing and return the token.

        The token is stable while sharing stays enabled (repeat calls keep the
        same URL), but rotates when re-enabling after a revoke so previously
        leaked links stay dead.
        """
        result = await db.execute(
            text(
                """
                UPDATE tasks
                SET share_token = CASE
                        WHEN share_enabled THEN COALESCE(share_token, :share_token)
                        ELSE :share_token
                    END,
                    share_enabled = TRUE,
                    updated_at = NOW()
                WHERE id = :task_id
                RETURNING share_token
                """
            ),
            {"task_id": task_id, "share_token": share_token},
        )
        await db.commit()
        row = result.fetchone()
        return str(row.share_token) if row else None

    @staticmethod
    async def disable_sharing(db: AsyncSession, task_id: str) -> bool:
        """Disable an existing share URL without changing private task access."""
        result = await db.execute(
            text(
                """
                UPDATE tasks
                SET share_enabled = FALSE,
                    updated_at = NOW()
                WHERE id = :task_id
                RETURNING id
                """
            ),
            {"task_id": task_id},
        )
        await db.commit()
        return result.fetchone() is not None

    @staticmethod
    async def get_shared_task_id(
        db: AsyncSession, share_token: str
    ) -> Optional[str]:
        """Resolve an enabled opaque share token to its task ID."""
        result = await db.execute(
            text(
                """
                SELECT id
                FROM tasks
                WHERE share_token = :share_token
                  AND share_enabled = TRUE
                  AND status = 'completed'
                LIMIT 1
                """
            ),
            {"share_token": share_token},
        )
        row = result.fetchone()
        return str(row.id) if row else None

    @staticmethod
    async def user_exists(db: AsyncSession, user_id: str) -> bool:
        """Check if a user exists in the database."""
        result = await db.execute(
            text("SELECT 1 FROM users WHERE id = :user_id"), {"user_id": user_id}
        )
        return result.fetchone() is not None

    @staticmethod
    async def delete_task(db: AsyncSession, task_id: str) -> None:
        """Delete a task by ID."""
        await db.execute(
            text("DELETE FROM tasks WHERE id = :task_id"), {"task_id": task_id}
        )
        await db.commit()
        logger.info(f"Deleted task {task_id}")

    @staticmethod
    async def get_task_notification_context(
        db: AsyncSession, task_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    t.id,
                    u.notify_on_completion,
                    t.completion_notification_sent_at,
                    s.title AS source_title,
                    u.email AS user_email,
                    u.name AS user_name,
                    u.first_name AS user_first_name
                FROM tasks t
                JOIN users u ON u.id = t.user_id
                LEFT JOIN sources s ON s.id = t.source_id
                WHERE t.id = :task_id
                """
            ),
            {"task_id": task_id},
        )
        row = result.fetchone()
        if not row:
            return None

        return {
            "task_id": row.id,
            "notify_on_completion": getattr(row, "notify_on_completion", False),
            "completion_notification_sent_at": getattr(
                row, "completion_notification_sent_at", None
            ),
            "source_title": getattr(row, "source_title", None),
            "user_email": getattr(row, "user_email", None),
            "user_name": getattr(row, "user_name", None),
            "user_first_name": getattr(row, "user_first_name", None),
        }

    @staticmethod
    async def mark_completion_notification_sent(
        db: AsyncSession, task_id: str
    ) -> bool:
        result = await db.execute(
            text(
                """
                UPDATE tasks
                SET completion_notification_sent_at = NOW(),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND completion_notification_sent_at IS NULL
                RETURNING completion_notification_sent_at
                """
            ),
            {"task_id": task_id},
        )
        await db.commit()
        return result.fetchone() is not None
