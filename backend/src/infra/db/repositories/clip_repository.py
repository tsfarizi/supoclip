"""
Clip repository - handles all database operations for generated clips.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sa_text
from typing import List, Dict, Any, Optional
import logging

from ..models import generate_uuid_string

logger = logging.getLogger(__name__)


class ClipRepository:
    """Repository for clip-related database operations."""

    @staticmethod
    async def create_clip(
        db: AsyncSession,
        task_id: str,
        filename: str,
        file_path: str,
        start_time: str,
        end_time: str,
        duration: float,
        text: str,
        relevance_score: float,
        reasoning: str,
        clip_order: int,
        virality_score: int = 0,
        hook_score: int = 0,
        engagement_score: int = 0,
        value_score: int = 0,
        shareability_score: int = 0,
        hook_type: Optional[str] = None,
        hook_title: Optional[str] = None,
        description: Optional[str] = None,
        hashtags: Optional[list[str]] = None,
        metadata_status: str = "pending",
        metadata_version: Optional[str] = None,
        metadata_prompt_version: Optional[str] = None,
        composition_json: Optional[str] = None,
        composition_version: int = 1,
    ) -> str:
        """Create a new clip record and return its ID."""
        import json as _json

        clip_id = generate_uuid_string()
        hashtags_json = _json.dumps(hashtags or [], ensure_ascii=False) if hashtags is not None else None
        # Clamp metadata_status
        if metadata_status not in ("pending", "ready", "degraded", "failed"):
            metadata_status = "pending"
        result = await db.execute(
            sa_text("""
                INSERT INTO generated_clips
                (id, task_id, filename, file_path, start_time, end_time, duration,
                 text, relevance_score, reasoning, clip_order,
                 virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type,
                 hook_title, description, hashtags, metadata_status, metadata_version, metadata_prompt_version,
                 composition_json, composition_version, created_at)
                VALUES
                (:clip_id, :task_id, :filename, :file_path, :start_time, :end_time, :duration,
                 :text, :relevance_score, :reasoning, :clip_order,
                 :virality_score, :hook_score, :engagement_score, :value_score, :shareability_score, :hook_type,
                 :hook_title, :description, :hashtags, :metadata_status, :metadata_version, :metadata_prompt_version,
                 :composition_json, :composition_version, NOW())
                RETURNING id
            """),
            {
                "clip_id": clip_id,
                "task_id": task_id,
                "filename": filename,
                "file_path": file_path,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "text": text,
                "relevance_score": relevance_score,
                "reasoning": reasoning,
                "clip_order": clip_order,
                "virality_score": virality_score,
                "hook_score": hook_score,
                "engagement_score": engagement_score,
                "value_score": value_score,
                "shareability_score": shareability_score,
                "hook_type": hook_type,
                "hook_title": hook_title,
                "description": description,
                "hashtags": hashtags_json,
                "metadata_status": metadata_status,
                "metadata_version": metadata_version,
                "metadata_prompt_version": metadata_prompt_version,
                "composition_json": composition_json,
                "composition_version": composition_version,
            },
        )
        await db.commit()
        clip_id = result.scalar()
        if not clip_id:
            raise RuntimeError("Failed to create clip: no ID returned")
        logger.debug(f"Created clip {clip_id} for task {task_id}")
        return str(clip_id)

    @staticmethod
    async def get_clips_by_task(db: AsyncSession, task_id: str) -> List[Dict[str, Any]]:
        """Get all clips for a specific task, ordered by clip_order."""
        import json as _json

        result = await db.execute(
            sa_text("""
                SELECT id, filename, file_path, start_time, end_time, duration,
                       text, relevance_score, reasoning, clip_order, created_at,
                       virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type,
                       hook_title, description, hashtags, metadata_status, metadata_version, metadata_prompt_version,
                       composition_json, composition_version
                FROM generated_clips
                WHERE task_id = :task_id
                ORDER BY clip_order ASC
            """),
            {"task_id": task_id},
        )

        clips = []
        for row in result.fetchall():
            try:
                hashtags = _json.loads(row.hashtags) if row.hashtags else []
                if not isinstance(hashtags, list):
                    hashtags = []
            except Exception:
                hashtags = []
            clips.append(
                {
                    "id": row.id,
                    "filename": row.filename,
                    "file_path": row.file_path,
                    "start_time": row.start_time,
                    "end_time": row.end_time,
                    "duration": row.duration,
                    "text": row.text,
                    "relevance_score": row.relevance_score,
                    "reasoning": row.reasoning,
                    "clip_order": row.clip_order,
                    "created_at": row.created_at.isoformat(),
                    "video_url": f"/tasks/{task_id}/clips/{row.id}/file",
                    "virality_score": row.virality_score or 0,
                    "hook_score": row.hook_score or 0,
                    "engagement_score": row.engagement_score or 0,
                    "value_score": row.value_score or 0,
                    "shareability_score": row.shareability_score or 0,
                    "hook_type": row.hook_type,
                    "hook_title": row.hook_title,
                    "description": row.description,
                    "hashtags": hashtags,
                    "metadata_status": row.metadata_status or "pending",
                    "metadata_version": row.metadata_version,
                    "metadata_prompt_version": row.metadata_prompt_version,
                    "composition_json": getattr(row, "composition_json", None),
                    "composition_version": getattr(row, "composition_version", 1) or 1,
                }
            )

        return clips

    @staticmethod
    async def get_clips_count(db: AsyncSession, task_id: str) -> int:
        """Get the count of clips for a task."""
        result = await db.execute(
            sa_text(
                "SELECT COUNT(*) as count FROM generated_clips WHERE task_id = :task_id"
            ),
            {"task_id": task_id},
        )
        return result.scalar()

    @staticmethod
    async def delete_clips_by_task(db: AsyncSession, task_id: str) -> int:
        """Delete all clips for a task. Returns count of deleted clips."""
        result = await db.execute(
            sa_text("DELETE FROM generated_clips WHERE task_id = :task_id"),
            {"task_id": task_id},
        )
        await db.commit()
        deleted_count = result.rowcount
        logger.info(f"Deleted {deleted_count} clips for task {task_id}")
        return deleted_count

    @staticmethod
    async def delete_clip(db: AsyncSession, clip_id: str) -> None:
        """Delete a single clip by ID."""
        await db.execute(
            sa_text("DELETE FROM generated_clips WHERE id = :clip_id"),
            {"clip_id": clip_id},
        )
        await db.commit()
        logger.info(f"Deleted clip {clip_id}")

    @staticmethod
    async def get_clip_by_id(
        db: AsyncSession, clip_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get one clip by ID."""
        import json as _json

        result = await db.execute(
            sa_text(
                """
                SELECT id, task_id, filename, file_path, start_time, end_time, duration,
                       text, relevance_score, reasoning, clip_order,
                       virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type,
                       hook_title, description, hashtags, metadata_status, metadata_version, metadata_prompt_version,
                       composition_json, composition_version, created_at
                FROM generated_clips
                WHERE id = :clip_id
                """
            ),
            {"clip_id": clip_id},
        )
        row = result.fetchone()
        if not row:
            return None
        try:
            hashtags = _json.loads(row.hashtags) if row.hashtags else []
            if not isinstance(hashtags, list):
                hashtags = []
        except Exception:
            hashtags = []

        return {
            "id": row.id,
            "task_id": row.task_id,
            "filename": row.filename,
            "file_path": row.file_path,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "duration": row.duration,
            "text": row.text,
            "relevance_score": row.relevance_score,
            "reasoning": row.reasoning,
            "clip_order": row.clip_order,
            "virality_score": row.virality_score or 0,
            "hook_score": row.hook_score or 0,
            "engagement_score": row.engagement_score or 0,
            "value_score": row.value_score or 0,
            "shareability_score": row.shareability_score or 0,
            "hook_type": row.hook_type,
            "hook_title": row.hook_title,
            "description": row.description,
            "hashtags": hashtags,
            "metadata_status": row.metadata_status or "pending",
            "metadata_version": row.metadata_version,
            "metadata_prompt_version": row.metadata_prompt_version,
            "composition_json": getattr(row, "composition_json", None),
            "composition_version": getattr(row, "composition_version", 1) or 1,
            "created_at": row.created_at.isoformat(),
            "video_url": f"/tasks/{row.task_id}/clips/{row.id}/file",
        }

    @staticmethod
    async def update_clip(
        db: AsyncSession,
        clip_id: str,
        filename: str,
        file_path: str,
        start_time: str,
        end_time: str,
        duration: float,
        text: str,
    ) -> None:
        """Update core clip metadata and file path."""
        await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET filename = :filename,
                    file_path = :file_path,
                    start_time = :start_time,
                    end_time = :end_time,
                    duration = :duration,
                    text = :text,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "filename": filename,
                "file_path": file_path,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "text": text,
            },
        )
        await db.commit()

    @staticmethod
    async def reorder_task_clips(db: AsyncSession, task_id: str) -> None:
        """Normalize clip_order sequence after edits."""
        result = await db.execute(
            sa_text(
                "SELECT id FROM generated_clips WHERE task_id = :task_id ORDER BY clip_order ASC, created_at ASC"
            ),
            {"task_id": task_id},
        )
        clip_ids = [row.id for row in result.fetchall()]
        for idx, cid in enumerate(clip_ids, start=1):
            await db.execute(
                sa_text(
                    "UPDATE generated_clips SET clip_order = :clip_order, updated_at = NOW() WHERE id = :clip_id"
                ),
                {"clip_order": idx, "clip_id": cid},
            )
        await db.commit()

    @staticmethod
    async def update_clip_metadata(
        db: AsyncSession,
        clip_id: str,
        description: str,
        hashtags: list[str],
        metadata_status: str = "ready",
        metadata_version: str | None = None,
        metadata_prompt_version: str | None = None,
    ) -> bool:
        """Update per-clip marketing metadata (description/hashtags) with CAS on existence."""
        import json as _json

        hashtags_json = _json.dumps(hashtags or [], ensure_ascii=False)
        if metadata_status not in ("pending", "ready", "degraded", "failed"):
            metadata_status = "ready"
        result = await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET description = :description,
                    hashtags = :hashtags,
                    metadata_status = :metadata_status,
                    metadata_version = :metadata_version,
                    metadata_prompt_version = :metadata_prompt_version,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "description": description,
                "hashtags": hashtags_json,
                "metadata_status": metadata_status,
                "metadata_version": metadata_version,
                "metadata_prompt_version": metadata_prompt_version,
            },
        )
        await db.commit()
        return bool(result.rowcount)

    @staticmethod
    async def update_clip_render_result(
        db: AsyncSession,
        clip_id: str,
        filename: str,
        file_path: str,
        duration: float,
        composition_json: Optional[str] = None,
        composition_version: int = 1,
    ) -> bool:
        """Update clip after re-rendering composition."""
        result = await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET filename = :filename,
                    file_path = :file_path,
                    duration = :duration,
                    composition_json = COALESCE(:composition_json, composition_json),
                    composition_version = :composition_version,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "filename": filename,
                "file_path": file_path,
                "duration": duration,
                "composition_json": composition_json,
                "composition_version": composition_version,
            },
        )
        await db.commit()
        return bool(result.rowcount)

    @staticmethod
    async def update_clip_composition(
        db: AsyncSession,
        clip_id: str,
        composition_json: str,
        composition_version: int = 1,
        expected_version: Optional[int] = None,
    ) -> bool:
        """Update clip composition JSON and composition version.

        When expected_version is provided, the UPDATE includes a CAS guard:
        the row is only modified if its current composition_version matches.
        Returns False (rowcount == 0) on version mismatch.
        """
        if expected_version is not None:
            result = await db.execute(
                sa_text(
                    """
                    UPDATE generated_clips
                    SET composition_json = :composition_json,
                        composition_version = :composition_version,
                        updated_at = NOW()
                    WHERE id = :clip_id
                      AND composition_version = :expected_version
                    """
                ),
                {
                    "clip_id": clip_id,
                    "composition_json": composition_json,
                    "composition_version": composition_version,
                    "expected_version": expected_version,
                },
            )
        else:
            result = await db.execute(
                sa_text(
                    """
                    UPDATE generated_clips
                    SET composition_json = :composition_json,
                        composition_version = :composition_version,
                        updated_at = NOW()
                    WHERE id = :clip_id
                    """
                ),
                {
                    "clip_id": clip_id,
                    "composition_json": composition_json,
                    "composition_version": composition_version,
                },
            )
        await db.commit()
        return bool(result.rowcount)
