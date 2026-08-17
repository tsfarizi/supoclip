"""
Video service - handles video processing business logic.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable
import logging
import json
import subprocess
import uuid
import math

from ..utils.async_helpers import run_in_thread
from ..youtube_utils import (
    async_download_youtube_video,
    async_get_youtube_video_info,
    async_get_youtube_video_title,
    get_youtube_video_id,
)
from ..video_utils import (
    get_video_transcript,
    get_video_transcript_local,
    create_clips_with_transitions,
    create_optimized_clip,
    apply_broll_suggestions_to_clip,
    parse_timestamp_to_seconds,
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    build_clip_signal_summary,
    extend_keep_ranges_to_sentence_boundary,
    load_cached_transcript_data,
    seconds_to_mmss,
    mix_clip_audio_with_sfx,
)
from ..clip_source_map import (
    normalize_source_ranges,
    save_clip_source_manifest,
    save_clip_source_ranges,
)
from ..transition_engine import compose_hook_and_main
from ..ai import get_most_relevant_parts_by_transcript
from ..config import get_config
from ..errors import CancelledError, DownloadError, InvalidSourceError

logger = logging.getLogger(__name__)
UPLOAD_URL_PREFIX = "upload://"


class VideoService:
    """Service for video processing operations."""

    @staticmethod
    def _get_file_duration(path: Path) -> Optional[float]:
        """Return video duration in seconds via ffprobe, or None on failure."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    str(path),
                ],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        except Exception:
            return None

    @staticmethod
    def _source_to_local_timestamp(timestamp: float, hook_range: tuple[float, float], main_ranges: List[tuple[float, float]], placement: str) -> float | None:
        """Map source time onto the rendered timeline, including crossfade overlap."""
        from ..video_utils import crossfade_fade_for_ranges

        ranges = [hook_range, *main_ranges]
        fade = crossfade_fade_for_ranges(ranges)
        cursor = 0.0
        for index, (start, end) in enumerate(ranges):
            duration = end - start
            if start <= timestamp < end:
                if placement == "transition":
                    return cursor + (max(0.0, duration - fade) if index == 0 else 0.0)
                return cursor + timestamp - start
            cursor += duration
            if index < len(ranges) - 1:
                cursor -= fade
        return None

    @staticmethod
    async def _apply_sound_effects(
        clip_path: Path,
        segment: Dict[str, Any],
        hook_range: tuple[float, float],
        main_ranges: List[tuple[float, float]],
        task_id: Optional[str],
        count: int,
    ) -> None:
        payload = {"placements": [], "attribution": [], "degraded": False}
        if count <= 0 or not task_id:
            try:
                clip_path.with_suffix(".sfx.json").write_text(json.dumps(payload), encoding="utf-8")
            except OSError:
                logger.warning("SFX sidecar write degraded", exc_info=True)
            return
        try:
            from ..freesound import download_freesound_mp3, search_freesound_sounds
            from ..sound_effect_cache import (
                cache_sound_effect,
                get_cached_sound_effect,
                write_attribution_manifest,
                read_attribution_manifest,
            )
            work_dir = Path(get_config().temp_dir) / "sfx-downloads" / task_id
            seen: set[float] = set()
            private_placements: list[dict[str, Any]] = []
            opportunities = segment.get("sfx_opportunities") or []
            for raw in opportunities[:count]:
                try:
                    timestamp = parse_timestamp_to_seconds(str(raw["source_timestamp"]))
                    key = round(timestamp, 3)
                    if key in seen:
                        continue
                    local_start = VideoService._source_to_local_timestamp(
                        timestamp, hook_range, main_ranges, str(raw.get("placement", "main"))
                    )
                    if local_start is None:
                        continue
                    seen.add(key)
                    sounds = await search_freesound_sounds(str(raw["query"]), limit=5)
                    if not sounds:
                        payload["degraded"] = True
                        continue
                    sound = sounds[0]
                    cached = get_cached_sound_effect("freesound", sound.id)
                    if cached is None:
                        downloaded = await download_freesound_mp3(sound, work_dir / f"{sound.id}.mp3")
                        cached = cache_sound_effect(sound, downloaded, task_id)
                    else:
                        existing = ()
                        try:
                            existing = read_attribution_manifest(task_id)
                        except Exception:
                            pass
                        try:
                            write_attribution_manifest(task_id, (*existing, cached.attribution))
                        except Exception:
                            payload["degraded"] = True
                            logger.warning("SFX attribution write degraded", exc_info=True)
                    duration = min(float(raw.get("duration", 1.0)), 5.0)
                    placement = {
                        "asset_path": str(cached.path),
                        "start_seconds": max(0.0, local_start),
                        "end_seconds": max(0.0, local_start) + duration,
                        "volume": 10 ** (float(raw.get("gain_db", 0.0)) / 20),
                        "placement": str(raw.get("placement", "main")),
                    }
                    payload["placements"].append({
                        "start_seconds": placement["start_seconds"],
                        "end_seconds": placement["end_seconds"],
                        "placement": placement["placement"],
                        "title": sound.name,
                        "creator": sound.username,
                    })
                    payload["attribution"].append({
                        "sound_id": str(sound.id), "title": sound.name,
                        "creator": sound.username, "license": sound.license,
                        "source_url": sound.url,
                    })
                    private_placements.append(placement)
                except Exception as exc:
                    payload["degraded"] = True
                    logger.warning("SFX opportunity degraded: %s", type(exc).__name__)
            if payload["placements"]:
                output = clip_path.with_suffix(".sfx.mp4")
                result = await run_in_thread(mix_clip_audio_with_sfx, clip_path, private_placements, output)
                if result.output_path and Path(result.output_path).is_file():
                    clip_path.unlink()
                    Path(result.output_path).rename(clip_path)
                else:
                    payload["degraded"] = True
        except Exception as exc:
            payload["degraded"] = True
            logger.warning("SFX integration degraded: %s", type(exc).__name__)
        try:
            clip_path.with_suffix(".sfx.json").write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            logger.warning("SFX sidecar write degraded", exc_info=True)

    @staticmethod
    def _hook_range(segment: Dict[str, Any], main_start: float) -> tuple[float, float] | None:
        selection = segment.get("hook_selection")
        if hasattr(selection, "model_dump"):
            selection = selection.model_dump()
        if not isinstance(selection, dict):
            return None
        try:
            start = parse_timestamp_to_seconds(str(selection["hook_start_time"]))
            end = parse_timestamp_to_seconds(str(selection["hook_end_time"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        if not (1.0 <= end - start <= 5.0 and 0 <= start < end <= main_start):
            return None
        return start, end

    @staticmethod
    def _deterministic_fallback_hook(
        segment: Dict[str, Any],
        main_start: float,
        main_ranges: List[tuple[float, float]],
    ) -> tuple[tuple[float, float], List[tuple[float, float]]] | None:
        """Derive a source-backed hook for manifests created before hook selection."""
        available_ranges = normalize_source_ranges(
            segment.get("source_ranges") or main_ranges
        )
        for range_start, range_end in reversed(available_ranges):
            hook_end = min(main_start, range_end)
            hook_start = max(range_start, hook_end - 5.0)
            if 1.0 <= hook_end - hook_start <= 5.0 and hook_start < hook_end:
                return (hook_start, hook_end), main_ranges

        if not main_ranges:
            return None
        range_start, range_end = main_ranges[0]
        hook_end = min(range_end, range_start + 5.0)
        if hook_end - range_start < 1.0:
            return None

        # A clip beginning at source time zero has no earlier source frame. Use
        # its leading source range as the deterministic hook and remove that
        # prefix from the main ranges so the compositor still has hook -> main
        # ordering rather than rendering the same frames twice.
        hook = (range_start, hook_end)
        remaining: List[tuple[float, float]] = []
        for start, end in main_ranges:
            if end <= hook_end:
                continue
            remaining.append((max(start, hook_end), end))
        return hook, normalize_source_ranges(remaining)

    @staticmethod
    def _resolve_hook(
        segment: Dict[str, Any],
        main_start: float,
        main_ranges: List[tuple[float, float]],
    ) -> tuple[tuple[float, float], List[tuple[float, float]]] | None:
        hook_range = VideoService._hook_range(segment, main_start)
        if hook_range is not None:
            return hook_range, main_ranges

        fallback = VideoService._deterministic_fallback_hook(
            segment, main_start, main_ranges
        )
        if fallback is None:
            return None
        hook_range, resolved_main_ranges = fallback
        segment["hook_selection"] = {
            "hook_start_time": seconds_to_mmss(hook_range[0]),
            "hook_end_time": seconds_to_mmss(hook_range[1]),
            "transcript_evidence": "Deterministic source-video fallback range.",
            "reasoning": "Legacy segment had no usable nested hook selection.",
            "hook_score": segment.get("hook_score", 0),
            "deterministic_fallback": True,
        }
        return hook_range, resolved_main_ranges

    @staticmethod
    def _build_fallback_segment(
        video_duration: Optional[float],
        transcript: str,
        target_duration: int,
    ) -> Dict[str, Any]:
        """Create a bounded starter clip when AI analysis selects no segments."""
        fallback_duration = max(1.0, float(target_duration or 30))
        if video_duration and video_duration > 0:
            fallback_duration = min(fallback_duration, max(1.0, video_duration))

        transcript_preview = " ".join((transcript or "").split())
        if len(transcript_preview) > 240:
            transcript_preview = f"{transcript_preview[:237]}..."

        return {
            "start_time": "00:00",
            "end_time": seconds_to_mmss(fallback_duration),
            "text": transcript_preview,
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so SupoClip generated the first available portion of the video."
            ),
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
            "hook_title": None,
        }

    @staticmethod
    async def _attach_broll_suggestions(
        relevant_parts: Any,
        segments_json: List[Dict[str, Any]],
        video_path: Path,
        task_id: Optional[str],
        output_format: str,
    ) -> None:
        """Fetch stock footage for AI-detected B-roll opportunities and attach them to segments.

        Each fetched suggestion keeps its absolute source-video timestamp; the render step
        maps it into clip-local time through the clip's keep ranges. Opportunities outside
        every chosen segment are dropped. Failures degrade gracefully: the task still renders
        clips without B-roll.
        """
        opportunities = getattr(relevant_parts, "broll_opportunities", None) or []
        if not opportunities:
            return

        runtime_config = get_config()
        if not runtime_config.pexels_api_key:
            logger.warning(
                "include_broll is on but PEXELS_API_KEY is not configured; skipping B-roll"
            )
            return

        # Cap API load per task: 2-4 per segment x up to 5 segments can exceed rate limits.
        opportunities = list(opportunities)[:8]

        from ..broll import fetch_broll_for_opportunities

        broll_dir = (
            Path(runtime_config.temp_dir) / "broll" / (task_id or "unknown")
        )
        orientation = "portrait" if output_format == "vertical" else "landscape"
        suggestions = await fetch_broll_for_opportunities(
            [o.model_dump() if hasattr(o, "model_dump") else o for o in opportunities],
            broll_dir,
            orientation=orientation,
        )

        for suggestion in suggestions:
            target_segment = None
            for segment in segments_json:
                start_seconds = parse_timestamp_to_seconds(
                    str(segment.get("start_time") or "00:00")
                )
                end_seconds = parse_timestamp_to_seconds(
                    str(segment.get("end_time") or "00:00")
                )
                if start_seconds <= suggestion.timestamp <= end_seconds:
                    target_segment = segment
                    break
            if target_segment is None:
                logger.info(
                    f"B-roll suggestion at {suggestion.timestamp:.1f}s outside all segments; dropping"
                )
                continue
            target_segment.setdefault("broll_suggestions", []).append(
                suggestion.model_dump()
            )

        logger.info(
            f"Attached B-roll suggestions to {sum(1 for s in segments_json if s.get('broll_suggestions'))} segments"
        )

    @staticmethod
    def resolve_local_video_path(url: str) -> Path:
        """Resolve uploaded-video references without exposing server filesystem paths."""
        if url.startswith(UPLOAD_URL_PREFIX):
            filename = Path(url.removeprefix(UPLOAD_URL_PREFIX)).name
            return Path(get_config().temp_dir) / "uploads" / filename
        raise ValueError("Only upload:// references are allowed for local video sources")

    @staticmethod
    async def download_video(url: str, task_id: Optional[str] = None) -> Optional[Path]:
        """
        Download a YouTube video asynchronously.
        """
        logger.info(f"Starting video download: {url}")
        video_path = await async_download_youtube_video(url, 3, task_id)

        if not video_path:
            logger.error(f"Failed to download video: {url}")
            return None

        logger.info(f"Video downloaded successfully: {video_path}")
        return video_path

    @staticmethod
    async def get_video_title(url: str) -> str:
        """
        Get video title asynchronously.
        Returns a default title if retrieval fails.
        """
        try:
            title = await async_get_youtube_video_title(url)
            return title or "YouTube Video"
        except Exception as e:
            logger.warning(f"Failed to get video title: {e}")
            return "YouTube Video"

    @staticmethod
    async def generate_transcript(
        video_path: Path, processing_mode: str = "balanced"
    ) -> str:
        """
        Generate transcript from video using AssemblyAI.
        Runs in thread pool to avoid blocking.
        """
        logger.info(f"Generating transcript for: {video_path}")
        speech_model = "best"
        runtime_config = get_config()
        if processing_mode == "fast":
            speech_model = runtime_config.fast_mode_transcript_model

        if runtime_config.transcript_provider == "local_asr":
            transcript = await run_in_thread(
                get_video_transcript_local, video_path, speech_model
            )
        else:
            transcript = await run_in_thread(
                get_video_transcript, video_path, speech_model
            )
        logger.info(f"Transcript generated: {len(transcript)} characters")
        return transcript

    @staticmethod
    async def analyze_transcript(
        transcript: str,
        clip_signals: Optional[str] = None,
        include_broll: bool = False,
        visual_signals: Optional[str] = None,
        max_sfx_count: int = 0,
    ) -> Any:
        """
        Analyze transcript with AI to find relevant segments.
        This is already async, no need to wrap.
        """
        logger.info("Starting AI analysis of transcript")
        relevant_parts = await get_most_relevant_parts_by_transcript(
            transcript,
            clip_signals=clip_signals,
            include_broll=include_broll,
            visual_signals=visual_signals,
            max_sfx_count=max_sfx_count,
        )
        logger.info(
            f"AI analysis complete: {len(relevant_parts.most_relevant_segments)} segments found"
        )
        return relevant_parts

    @staticmethod
    async def create_video_clips(
        video_path: Path,
        segments: List[Dict[str, Any]],
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        hook_persist: bool = False,
        watermark: Optional[str] = None,
        watermark_persist: bool = False,
        task_id: Optional[str] = None,
        sound_effects_count: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Create standalone video clips from segments with optional subtitles.
        Runs in thread pool as video processing is CPU-intensive.
        output_format: 'vertical' (9:16) or 'original' (1080x1920 white canvas).
        add_subtitles: False skips subtitles; the hook title still renders when
        present, and hook_persist keeps it on screen for the whole clip.
        """
        logger.info(f"Creating {len(segments)} video clips subtitles={add_subtitles}")
        clips_output_dir = Path(get_config().temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        if not segments:
            clips_info = await run_in_thread(
                create_clips_with_transitions,
                video_path,
                segments,
                clips_output_dir,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
                cleanup_settings,
                hook_persist=hook_persist,
                watermark=watermark,
                watermark_persist=watermark_persist,
            )
        else:
            clips_info = []
            for index, segment in enumerate(segments):
                clip_info = await VideoService.create_single_clip(
                    video_path,
                    segment,
                    index,
                    clips_output_dir,
                    font_family,
                    font_size,
                    font_color,
                    caption_template,
                    output_format,
                    add_subtitles,
                    cleanup_settings,
                    hook_persist=hook_persist,
                    watermark=watermark,
                    watermark_persist=watermark_persist,
                    task_id=task_id,
                    sound_effects_count=sound_effects_count,
                )
                if clip_info is not None:
                    clips_info.append(clip_info)

        logger.info(f"Successfully created {len(clips_info)} clips")
        return clips_info

    @staticmethod
    async def create_single_clip(
        video_path: Path,
        segment: Dict[str, Any],
        clip_index: int,
        output_dir: Path,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        hook_persist: bool = False,
        watermark: Optional[str] = None,
        watermark_persist: bool = False,
        task_id: Optional[str] = None,
        sound_effects_count: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Render a single clip in the thread pool and return clip_info dict, or None on failure."""
        try:
            provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
            source_ranges = normalize_source_ranges(segment.get("source_ranges"))
            if provided_keep_ranges:
                start_seconds = provided_keep_ranges[0][0]
                end_seconds = provided_keep_ranges[-1][1]
            elif source_ranges:
                start_seconds = source_ranges[0][0]
                end_seconds = source_ranges[-1][1]
            else:
                start_seconds = parse_timestamp_to_seconds(segment["start_time"])
                end_seconds = parse_timestamp_to_seconds(segment["end_time"])
            duration = end_seconds - start_seconds

            if duration <= 0:
                logger.warning(
                    f"Skipping clip {clip_index + 1}: invalid duration {duration:.1f}s"
                )
                return None

            unique_suffix = uuid.uuid4().hex[:12]
            clip_filename = (
                f"clip_{clip_index + 1}_"
                f"{segment['start_time'].replace(':', '')}-"
                f"{segment['end_time'].replace(':', '')}_"
                f"{unique_suffix}.mp4"
            )
            clip_path = output_dir / clip_filename
            if provided_keep_ranges:
                keep_ranges = provided_keep_ranges
            elif source_ranges:
                keep_ranges = build_keep_ranges_from_source_ranges(
                    video_path,
                    source_ranges,
                    cleanup_settings,
                )
            else:
                keep_ranges = build_clip_keep_ranges(
                    video_path,
                    start_seconds,
                    end_seconds,
                    cleanup_settings,
                )
            keep_ranges = extend_keep_ranges_to_sentence_boundary(
                video_path, keep_ranges, pull_back_to_complete_sentence=True
            )

            resolved_hook = VideoService._resolve_hook(
                segment, start_seconds, keep_ranges
            )
            if resolved_hook is None:
                logger.error("Skipping clip %s: no source-grounded hook available", clip_index + 1)
                return None
            hook_range, keep_ranges = resolved_hook
            if not keep_ranges:
                logger.error("Skipping clip %s: fallback hook consumed the main range", clip_index + 1)
                return None
            success = await run_in_thread(
                compose_hook_and_main,
                video_path,
                hook_range,
                keep_ranges,
                clip_path,
                add_subtitles=add_subtitles,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                output_format=output_format,
                hook_title=segment.get("hook_title"),
                hook_persist=hook_persist,
                watermark=watermark,
                watermark_persist=watermark_persist,
            )

            if not success or not clip_path.is_file():
                logger.error(f"Failed to create clip {clip_index + 1}")
                return None

            save_clip_source_manifest(clip_path, keep_ranges, hook_range)
            broll_final = await run_in_thread(
                apply_broll_suggestions_to_clip,
                clip_path,
                segment.get("broll_suggestions") or [],
                keep_ranges,
            )
            if broll_final:
                clip_path.unlink()
                broll_final.rename(clip_path)
            await VideoService._apply_sound_effects(
                clip_path, segment, hook_range, keep_ranges, task_id, sound_effects_count
            )
            cleaned_duration = sum(end - start for start, end in keep_ranges)
            hook_selection = segment.get("hook_selection")
            is_deterministic_fallback = (
                isinstance(hook_selection, dict)
                and hook_selection.get("deterministic_fallback") is True
            )
            logger.info(
                f"Created clip {clip_index + 1}: {cleaned_duration:.1f}s"
            )
            return {
                "clip_id": clip_index + 1,
                "filename": clip_filename,
                "path": str(clip_path),
                "start_time": segment["start_time"],
                "end_time": segment["end_time"],
                "duration": cleaned_duration,
                "text": segment.get("text", ""),
                "relevance_score": segment.get("relevance_score", 0.0),
                "reasoning": segment.get("reasoning", ""),
                "virality_score": segment.get("virality_score", 0),
                "hook_score": segment.get("hook_score", 0),
                "engagement_score": segment.get("engagement_score", 0),
                "value_score": segment.get("value_score", 0),
                "shareability_score": segment.get("shareability_score", 0),
                "hook_type": "deterministic_fallback" if is_deterministic_fallback else segment.get("hook_type"),
                "hook_title": segment.get("hook_title"),
                "keep_ranges": keep_ranges,
            }
        except Exception as e:
            logger.error(f"Error creating clip {clip_index + 1}: {e}")
            return None

    @staticmethod
    async def apply_single_transition(
        prev_clip_path: Path,
        current_clip_info: Dict[str, Any],
        clip_index: int,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Return the original clip info.

        Standalone exports intentionally do not depend on adjacent clips.
        """
        logger.info(
            "Skipping inter-clip transition for clip %s to preserve standalone exports",
            clip_index + 1,
        )
        return current_clip_info

    @staticmethod
    def determine_source_type(url: str) -> str:
        """Determine if source is YouTube or uploaded file."""
        video_id = get_youtube_video_id(url)
        if video_id:
            return "youtube"
        if url.startswith(UPLOAD_URL_PREFIX):
            return "video_url"
        raise InvalidSourceError(
            "Only YouTube URLs or upload:// references are supported"
        )

    @staticmethod
    async def process_video_complete(
        url: str,
        source_type: str,
        task_id: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        include_broll: bool = False,
        sound_effects_count: int = 0,
        cached_transcript: Optional[str] = None,
        cached_analysis_json: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
        should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Dict[str, Any]:
        """
        Complete video processing pipeline.
        Returns dict with segments and clips info.

        progress_callback: Optional function to call with progress updates
                          Signature: async def callback(progress: int, message: str, status: str)
        """
        try:
            runtime_config = get_config()
            # Step 1: Get video path (download or use existing)
            if should_cancel and await should_cancel():
                raise CancelledError("Task cancelled")

            if progress_callback:
                await progress_callback(10, "Downloading video...", "processing")

            if source_type == "youtube":
                # Persistent video cache: a validated hit skips the metadata
                # preflight and the download entirely. The post-download
                # duration guard below still validates the cached file.
                from ..video_cache import lookup as cache_lookup

                video_id = get_youtube_video_id(url)
                cached = cache_lookup(video_id) if video_id else None
                if (
                    cached is not None
                    and runtime_config.video_cache_skip_metadata
                ):
                    if (
                        cached.duration_seconds
                        and cached.duration_seconds > runtime_config.max_video_duration
                    ):
                        mins = runtime_config.max_video_duration // 60
                        raise DownloadError(
                            f"Video is too long ({int(cached.duration_seconds) // 60} min). "
                            f"Maximum allowed duration is {mins} minutes."
                        )
                    video_path = Path(cached.path)
                    if not video_path.exists():
                        raise DownloadError("Cached video file not found")
                    logger.info(
                        "Using video cache for %s: %s",
                        video_id,
                        video_path.name,
                    )
                else:
                    video_info = await async_get_youtube_video_info(url, task_id=task_id)
                    if video_info:
                        duration = video_info.get("duration", 0)
                        if duration and duration > runtime_config.max_video_duration:
                            mins = runtime_config.max_video_duration // 60
                            raise DownloadError(
                                f"Video is too long ({duration // 60} min). "
                                f"Maximum allowed duration is {mins} minutes."
                            )

                    video_path = await VideoService.download_video(url, task_id=task_id)
                    if not video_path:
                        raise DownloadError("Failed to download video")
            else:
                video_path = VideoService.resolve_local_video_path(url)
                if not video_path.exists():
                    raise DownloadError("Video file not found")

            # Post-download duration guard (catches cases where preflight info was unavailable)
            file_duration = VideoService._get_file_duration(video_path)
            if file_duration and file_duration > runtime_config.max_video_duration:
                mins = runtime_config.max_video_duration // 60
                raise DownloadError(
                    f"Video is too long ({int(file_duration) // 60} min). "
                    f"Maximum allowed duration is {mins} minutes."
                )

            # Step 2: Generate transcript
            if should_cancel and await should_cancel():
                raise CancelledError("Task cancelled")

            if progress_callback:
                await progress_callback(30, "Generating transcript...", "processing")

            transcript = cached_transcript
            if not transcript or load_cached_transcript_data(video_path) is None:
                if transcript:
                    logger.info(
                        "Cached transcript present but word-timing sidecar missing; re-transcribing"
                    )
                try:
                    transcript = await VideoService.generate_transcript(
                        video_path, processing_mode=processing_mode
                    )
                except Exception as exc:
                    if not cached_transcript:
                        raise
                    logger.warning(
                        "Re-transcription failed (%s); falling back to cached transcript", exc
                    )
                    transcript = cached_transcript

            # Step 3: AI analysis
            if should_cancel and await should_cancel():
                raise CancelledError("Task cancelled")

            if progress_callback:
                await progress_callback(
                    50, "Analyzing content with AI...", "processing"
                )

            relevant_parts = None
            if cached_analysis_json:
                try:
                    cached_analysis = json.loads(cached_analysis_json)
                    segments = cached_analysis.get("most_relevant_segments", [])
                    if not segments:
                        logger.info(
                            "Ignoring cached transcript analysis with no clip segments"
                        )
                    else:

                        class _SimpleResult:
                            def __init__(self, payload: Dict[str, Any]):
                                self.summary = payload.get("summary")
                                self.key_topics = payload.get("key_topics")
                                self.most_relevant_segments = payload.get(
                                    "most_relevant_segments", []
                                )
                                self.broll_opportunities = payload.get(
                                    "broll_opportunities"
                                )
                                self.sfx_opportunities = payload.get("sfx_opportunities")

                        relevant_parts = _SimpleResult(
                            {
                                "summary": cached_analysis.get("summary"),
                                "key_topics": cached_analysis.get("key_topics", []),
                                "most_relevant_segments": segments,
                                "broll_opportunities": cached_analysis.get(
                                    "broll_opportunities"
                                ),
                                "sfx_opportunities": cached_analysis.get("sfx_opportunities"),
                            }
                        )
                except Exception:
                    relevant_parts = None

            if relevant_parts is None:
                try:
                    clip_signals = await run_in_thread(
                        build_clip_signal_summary,
                        video_path,
                        transcript,
                    )
                except Exception as exc:
                    logger.warning("Clip signal extraction failed: %s", exc)
                    clip_signals = None

                visual_signals = None
                try:
                    from ..visual_signals import build_visual_signal_summary

                    visual_signals = await run_in_thread(
                        build_visual_signal_summary,
                        video_path,
                        transcript,
                    )
                except Exception as exc:
                    logger.warning("Visual signal extraction failed: %s", exc)
                    visual_signals = None

                relevant_parts = await VideoService.analyze_transcript(
                    transcript,
                    clip_signals=clip_signals,
                    include_broll=include_broll,
                    max_sfx_count=sound_effects_count,
                    visual_signals=visual_signals,
                )

            # Step 4: Create clips
            if should_cancel and await should_cancel():
                raise CancelledError("Task cancelled")

            if progress_callback:
                await progress_callback(70, "Creating video clips...", "processing")

            raw_segments = relevant_parts.most_relevant_segments
            segments_json: List[Dict[str, Any]] = []
            for segment in raw_segments:
                if isinstance(segment, dict):
                    virality = segment.get("virality") or {}
                    if hasattr(virality, "model_dump"):
                        virality = virality.model_dump()
                    segments_json.append(
                        {
                            "start_time": segment.get("start_time"),
                            "end_time": segment.get("end_time"),
                            "text": segment.get("text", ""),
                            "relevance_score": segment.get("relevance_score", 0.0),
                            "reasoning": segment.get("reasoning", ""),
                            "virality_score": virality.get("total_score", 0),
                            "hook_score": virality.get("hook_score", 0),
                            "engagement_score": virality.get("engagement_score", 0),
                            "value_score": virality.get("value_score", 0),
                            "shareability_score": virality.get("shareability_score", 0),
                            "hook_type": virality.get("hook_type"),
                            "hook_title": segment.get("hook_title"),
                            "hook_selection": segment.get("hook_selection"),
                        }
                    )

                else:
                    virality = segment.virality.model_dump() if segment.virality else {}
                    segments_json.append(
                        {
                            "start_time": segment.start_time,
                            "end_time": segment.end_time,
                            "text": segment.text,
                            "relevance_score": segment.relevance_score,
                            "reasoning": segment.reasoning,
                            "virality_score": virality.get("total_score", 0),
                            "hook_score": virality.get("hook_score", 0),
                            "engagement_score": virality.get("engagement_score", 0),
                            "value_score": virality.get("value_score", 0),
                            "shareability_score": virality.get("shareability_score", 0),
                            "hook_type": virality.get("hook_type"),
                            "hook_title": getattr(segment, "hook_title", None),
                            "hook_selection": (
                                segment.hook_selection.model_dump()
                                if getattr(segment, "hook_selection", None) else None
                            ),
                        }
                    )

            if processing_mode == "fast":
                segments_json = segments_json[: runtime_config.fast_mode_max_clips]

            sfx_opportunities = [
                o.model_dump() if hasattr(o, "model_dump") else o
                for o in (getattr(relevant_parts, "sfx_opportunities", None) or [])
            ]
            assigned_sfx: set[float] = set()
            for segment in segments_json:
                start = parse_timestamp_to_seconds(str(segment.get("start_time") or "00:00"))
                end = parse_timestamp_to_seconds(str(segment.get("end_time") or "00:00"))
                remaining = max(0, min(5, sound_effects_count) - len(assigned_sfx))
                segment["sfx_opportunities"] = [
                    opportunity for opportunity in sfx_opportunities
                    if start <= parse_timestamp_to_seconds(str(opportunity.get("source_timestamp"))) <= end
                    and round(parse_timestamp_to_seconds(str(opportunity.get("source_timestamp"))), 3) not in assigned_sfx
                ][:remaining]
                assigned_sfx.update(
                    round(parse_timestamp_to_seconds(str(item.get("source_timestamp"))), 3)
                    for item in segment["sfx_opportunities"]
                )

            if not segments_json:
                logger.warning(
                    "AI analysis selected no segments; using fallback clip window"
                )
                segments_json = [
                    VideoService._build_fallback_segment(
                        file_duration,
                        transcript,
                        runtime_config.clip_duration,
                    )
                ]

            if include_broll:
                await VideoService._attach_broll_suggestions(
                    relevant_parts,
                    segments_json,
                    video_path,
                    task_id,
                    output_format,
                )

            return {
                "segments": segments_json,
                "segments_to_render": segments_json,
                "video_path": str(video_path),
                "clips": [],
                "summary": relevant_parts.summary if relevant_parts else None,
                "key_topics": relevant_parts.key_topics if relevant_parts else None,
                "transcript": transcript,
                "analysis_json": json.dumps(
                    {
                        "summary": relevant_parts.summary if relevant_parts else None,
                        "key_topics": relevant_parts.key_topics
                        if relevant_parts
                        else [],
                        "most_relevant_segments": segments_json,
                        "broll_opportunities": [
                            o.model_dump() if hasattr(o, "model_dump") else o
                            for o in (relevant_parts.broll_opportunities or [])
                        ]
                        if include_broll
                        else [],
                        "sfx_opportunities": sfx_opportunities,
                    }
                ),
            }

        except Exception as e:
            logger.error(f"Error in video processing pipeline: {e}")
            raise
