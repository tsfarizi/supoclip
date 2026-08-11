"""
Task metadata service - resolves task render settings from DB columns
with the legacy ``task_source:{task_id}`` Redis cache as fallback.
"""

from typing import Any, Dict
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..clip_cleanup import normalize_clip_cleanup_settings
from ..config import Config, get_config
from ..infra.redis_client import get_redis_client
from ..video_utils import VALID_OUTPUT_FORMATS

logger = logging.getLogger(__name__)


class TaskMetadataService:
    """Service for task metadata resolution."""

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()
        try:
            self.redis = get_redis_client()
        except Exception:
            # Redis is optional for metadata reads; the fallback path degrades
            # to defaults when no client can be constructed.
            self.redis = None

    async def _load_task_source_redis_payload(self, task_id: str) -> Dict[str, Any]:
        """Read the legacy ``task_source:{task_id}`` cache, tolerant of failure."""
        try:
            redis_client = self.redis or get_redis_client()
            payload = await redis_client.get(f"task_source:{task_id}")
        except Exception as exc:
            logger.warning(
                "Unable to read task source cache for task %s: %s", task_id, exc
            )
            return {}
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def load_source_settings(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve task render settings, DB-first with Redis as legacy fallback.

        The Schema v2 columns (output_format / add_subtitles /
        cleanup_settings_json) are the authority; a NULL column marks a legacy
        row that still depends on the task_source:{task_id} Redis cache. All
        values are normalized through the same validators as the old Redis-only
        path so legacy and fresh rows produce identical settings.
        """
        defaults = {
            "output_format": "vertical",
            "add_subtitles": True,
            **normalize_clip_cleanup_settings(),
        }

        output_format = task.get("output_format")
        add_subtitles = task.get("add_subtitles")
        cleanup_settings_json = task.get("cleanup_settings_json")

        needs_redis = (
            output_format is None
            or add_subtitles is None
            or cleanup_settings_json is None
        )
        payload: Dict[str, Any] = {}
        if needs_redis:
            payload = await self._load_task_source_redis_payload(task.get("id") or "")

        if output_format is None:
            redis_format = payload.get("output_format", defaults["output_format"])
            output_format = (
                redis_format
                if redis_format in VALID_OUTPUT_FORMATS
                else defaults["output_format"]
            )
        elif output_format not in VALID_OUTPUT_FORMATS:
            output_format = defaults["output_format"]

        if add_subtitles is None:
            redis_subtitles = payload.get("add_subtitles", defaults["add_subtitles"])
            add_subtitles = (
                redis_subtitles
                if isinstance(redis_subtitles, bool)
                else defaults["add_subtitles"]
            )
        elif not isinstance(add_subtitles, bool):
            add_subtitles = defaults["add_subtitles"]

        cleanup_payload: Dict[str, Any] = {}
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
                cleanup_payload = parsed_cleanup
            else:
                cleanup_payload = payload
        else:
            cleanup_payload = payload

        return {
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            **normalize_clip_cleanup_settings(
                cleanup_payload.get("cut_long_pauses"),
                cleanup_payload.get("pause_threshold_ms"),
                cleanup_payload.get("remove_filler_words"),
                cleanup_payload.get("filtered_words"),
            ),
        }
