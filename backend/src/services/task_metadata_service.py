"""
Task metadata service - resolves task render settings from the DB columns.
"""

from typing import Any, Dict
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..clip_cleanup import normalize_clip_cleanup_settings
from ..config import Config, get_config
from ..video_utils import VALID_OUTPUT_FORMATS

logger = logging.getLogger(__name__)


class TaskMetadataService:
    """Service for task metadata resolution."""

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()

    async def load_source_settings(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve task render settings from the Schema v2 columns only.

        The tasks columns are the single authority; a NULL column resolves to
        the same default the worker would apply. The legacy
        ``task_source:{task_id}`` Redis cache is gone (P4): no dual read path
        exists anymore. Legacy rows whose columns were never backfilled get
        the documented defaults instead of a silent Redis read.
        """
        defaults = {
            "output_format": "vertical",
            "add_subtitles": True,
            "hook_persist": False,
            "watermark": None,
            "watermark_persist": False,
            "sound_effects_count": 0,
            **normalize_clip_cleanup_settings(),
        }

        output_format = task.get("output_format")
        add_subtitles = task.get("add_subtitles")
        hook_persist = task.get("hook_persist")
        watermark = task.get("watermark")
        watermark_persist = task.get("watermark_persist")
        cleanup_settings_json = task.get("cleanup_settings_json")
        sound_effects_count = task.get("sound_effects_count")

        if output_format is None or output_format not in VALID_OUTPUT_FORMATS:
            output_format = defaults["output_format"]

        if not isinstance(add_subtitles, bool):
            add_subtitles = defaults["add_subtitles"]

        if not isinstance(hook_persist, bool):
            hook_persist = defaults["hook_persist"]

        if not isinstance(watermark, str):
            watermark = defaults["watermark"]

        if not isinstance(watermark_persist, bool):
            watermark_persist = defaults["watermark_persist"]

        try:
            sound_effects_count = max(0, min(5, int(sound_effects_count or 0)))
        except (TypeError, ValueError):
            sound_effects_count = defaults["sound_effects_count"]

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

        return {
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            "hook_persist": hook_persist,
            "watermark": watermark,
            "watermark_persist": watermark_persist,
            "sound_effects_count": sound_effects_count,
            **normalize_clip_cleanup_settings(
                cleanup_payload.get("cut_long_pauses"),
                cleanup_payload.get("pause_threshold_ms"),
                cleanup_payload.get("remove_filler_words"),
                cleanup_payload.get("filtered_words"),
            ),
        }