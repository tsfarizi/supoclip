"""
Transition engine: render inter-clip transitions via ffmpeg.

- merge_clips_with_transition: single-pass chained xfade render (or hard concat).
- overlay_transition_mp4: apply a transition MP4 at a clip boundary (overlay).
- apply_transitions_between_clips: orchestrator that stitches N clips.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from .clip_editor import merge_clip_files
from .config import get_config
from .transition_spec import (
    DEFAULT_FADE_SECONDS,
    MAX_TRANSITION_FILE_MB,
    MAX_TRANSITION_FILE_SECONDS,
    clamp_fade,
    normalize_transition_spec,
    resolve_transition_file,
    transition_kind,
    xfade_name,
)
from .video_utils import (
    DEFAULT_COMPOSITION_FADE_SECONDS,
    OUTPUT_FPS,
    ffprobe_duration,
    ffprobe_has_audio,
    ffprobe_video_size,
    render_hook_composition,
    run_ffmpeg_command,
)

logger = logging.getLogger(__name__)

MAX_MERGED_CLIPS = 8
_MIN_FADE_SECONDS = 0.06

_VIDEO_ENCODE_ARGS = [
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-crf",
    "19",
    "-pix_fmt",
    "yuv420p",
    "-r",
    str(OUTPUT_FPS),
]
_AUDIO_ENCODE_ARGS = ["-c:a", "aac", "-b:a", "192k"]

_ALPHA_PIX_FMT_MARKERS = ("yuva", "rgba", "bgra", "abgr", "gbra")
_ALPHA_CODECS = {"png", "qtrle", "prores", "ffv1"}


def _temp_output(prefix: str) -> Path:
    """Create a throwaway file inside a fresh temp dir (caller owns lifetime)."""
    return Path(tempfile.mkdtemp(prefix=f"supoclip_{prefix}_")) / (
        f"{prefix}_{uuid.uuid4().hex[:12]}.mp4"
    )


def _move_to(src: Path, dst: Path) -> None:
    """Move src to dst, falling back to copy+unlink for cross-volume moves."""
    try:
        shutil.move(str(src), str(dst))
    except OSError:
        shutil.copy2(str(src), str(dst))
        src.unlink(missing_ok=True)


def _discard_intermediate(path: Path) -> None:
    """Remove an intermediate file and its throwaway temp dir if we own it."""
    parent = path.parent
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    if parent.name.startswith("supoclip_"):
        shutil.rmtree(parent, ignore_errors=True)


def _hard_concat(paths: List[Path]) -> Path:
    """Concatenate clips with hard cuts via the repo's merge_clip_files."""
    output_dir = Path(tempfile.mkdtemp(prefix="supoclip_concat_"))
    try:
        return merge_clip_files(paths, output_dir)
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        stderr = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"ffmpeg concat failed: {stderr[-2000:]}") from exc


def _probe_video_stream_info(path: Path) -> Optional[Tuple[str, str]]:
    """Return (codec_name, pix_fmt) of the first video stream, or None."""
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        return None
    codec = ""
    pix_fmt = ""
    for line in result.stdout.splitlines():
        if line.startswith("codec_name="):
            codec = line.split("=", 1)[1].strip()
        elif line.startswith("pix_fmt="):
            pix_fmt = line.split("=", 1)[1].strip()
    if not codec or not pix_fmt:
        return None
    return codec, pix_fmt


def _transition_has_alpha(path: Path) -> bool:
    """Heuristic alpha detection from the video stream's codec/pixel format."""
    info = _probe_video_stream_info(path)
    if info is None:
        return False
    codec, pix_fmt = info
    if any(marker in pix_fmt for marker in _ALPHA_PIX_FMT_MARKERS):
        return True
    return codec in _ALPHA_CODECS


# --- single-pass chained xfade ---------------------------------------------


def _render_chained_xfade(
    paths: List[Path], durations: List[float], name: str, fade: float
) -> Path:
    n = len(paths)
    width, height = ffprobe_video_size(paths[0])
    has_audio = all(ffprobe_has_audio(p) for p in paths)

    parts: List[str] = []
    for idx, (path, duration) in enumerate(zip(paths, durations)):
        parts.append(
            f"[{idx}:v]trim=start=0:end={duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},format=yuv420p,"
            f"setsar=1[v{idx}]"
        )
        if has_audio:
            parts.append(
                f"[{idx}:a]atrim=start=0:end={duration:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000[a{idx}]"
            )

    cur_v = "[v0]"
    cumulative = durations[0]
    for i in range(1, n):
        offset = cumulative - fade
        out = f"[vx{i}]"
        parts.append(
            f"{cur_v}[v{i}]xfade=transition={name}:duration={fade:.3f}:"
            f"offset={offset:.3f}{out}"
        )
        cumulative = cumulative + durations[i] - fade
        cur_v = out

    map_args = ["-map", cur_v]
    if has_audio:
        cur_a = "[a0]"
        for i in range(1, n):
            out = f"[ax{i}]"
            parts.append(f"{cur_a}[a{i}]acrossfade=d={fade:.3f}{out}")
            cur_a = out
        map_args += ["-map", cur_a]
    else:
        map_args += ["-an"]

    output_path = _temp_output("xfade")
    command = ["ffmpeg", "-y"]
    for path in paths:
        command += ["-i", str(path)]
    command += ["-filter_complex", ";".join(parts), *map_args, *_VIDEO_ENCODE_ARGS]
    if has_audio:
        command += _AUDIO_ENCODE_ARGS
    command += ["-movflags", "+faststart", str(output_path)]

    result = run_ffmpeg_command(command, timeout=1800)
    if result.returncode != 0:
        _discard_intermediate(output_path)
        raise RuntimeError(f"ffmpeg xfade merge failed: {result.stderr[-2000:]}")
    return output_path


def merge_clips_with_transition(
    paths: List[Path],
    spec: str,
    fade_seconds: Optional[float] = None,
    *,
    hook_path: Optional[Path] = None,
) -> Path:
    """Merge clips into one video, applying the transition described by spec.

    "none" / unknown specs fall back to a hard concat. "xfade:<name>" renders
    a single ffmpeg pass chaining xfade (video) and acrossfade (audio).
    "file:<stem>" is owned by the orchestrator and hard-concats here.
    """
    clip_paths = [Path(p) for p in paths]
    if hook_path is not None:
        hook = Path(hook_path)
        if not hook.is_file():
            raise ValueError(f"Hook file does not exist: {hook}")
        clip_paths.insert(0, hook)
    if len(clip_paths) < 2:
        raise ValueError("merge_clips_with_transition requires at least 2 clips")
    for path in clip_paths:
        if not path.is_file():
            raise ValueError(f"Clip file does not exist: {path}")

    normalized = normalize_transition_spec(spec)
    if hook_path is not None and normalized == "none":
        normalized = "xfade:fade"
    name = xfade_name(normalized)
    if name is None:
        return _hard_concat(clip_paths)

    if len(clip_paths) > MAX_MERGED_CLIPS:
        raise ValueError(
            f"merge_clips_with_transition supports at most {MAX_MERGED_CLIPS} "
            f"clips, got {len(clip_paths)}"
        )

    durations = [ffprobe_duration(p) for p in clip_paths]
    configured_fade = getattr(get_config(), "clip_crossfade_seconds", None) or 0.0
    requested = (
        fade_seconds
        if fade_seconds is not None
        else (
            DEFAULT_COMPOSITION_FADE_SECONDS
            if hook_path is not None
            else configured_fade or DEFAULT_FADE_SECONDS
        )
    )
    fade = clamp_fade(requested, min(durations))
    if fade < _MIN_FADE_SECONDS:
        logger.warning(
            "Clips too short for a crossfade (shortest=%.2fs); hard concat fallback",
            min(durations),
        )
        return _hard_concat(clip_paths)

    return _render_chained_xfade(clip_paths, durations, name, fade)


def compose_hook_and_main(
    video_path: Path,
    hook_range: Tuple[float, float],
    main_ranges: List[Tuple[float, float]],
    output_path: Path,
    *,
    add_subtitles: bool = True,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    hook_title: Optional[str] = None,
    hook_persist: bool = False,
    watermark: Optional[str] = None,
    watermark_persist: bool = False,
    fade_seconds: float = DEFAULT_COMPOSITION_FADE_SECONDS,
) -> Path:
    """Render ``hook_range -> xfade -> main_ranges`` as actual media.

    This is the source-range entry point for generated clips.  Editor merge
    callers should use ``merge_clips_with_transition(..., hook_path=...)`` or
    ``apply_transitions_between_clips(..., hook_path=...)`` when the hook has
    already been rendered as a clip.
    """
    destination = Path(output_path)
    if not render_hook_composition(
        Path(video_path),
        hook_range,
        main_ranges,
        destination,
        add_subtitles=add_subtitles,
        font_family=font_family,
        font_size=font_size,
        font_color=font_color,
        caption_template=caption_template,
        output_format=output_format,
        hook_title=hook_title,
        hook_persist=hook_persist,
        watermark=watermark,
        watermark_persist=watermark_persist,
        transition_seconds=fade_seconds,
    ):
        raise RuntimeError("Hook composition renderer did not produce media")
    return destination


# --- transition MP4 overlay -------------------------------------------------


def _build_pair_render_command(
    clip_a: Path,
    clip_b: Path,
    transition: Optional[Path],
    dur_a: float,
    dur_b: float,
    window: float,
    width: int,
    height: int,
    output_path: Path,
) -> Tuple[List[str], bool]:
    """Build the ffmpeg command for one boundary render.

    Video timeline (content-preserving):
        [clip_a 0..D_a-T] + [transition window T] + [clip_b T..D_b]
    The window overlays the transition MP4 (alpha path) over clip_a's tail, or
    fades clip_a's tail into clip_b's head (fallback). Audio crosses over the
    window with acrossfade so it stays in sync with the shortened timeline.
    Returns (command, has_audio).
    """
    tail_start = dur_a - window
    has_audio = ffprobe_has_audio(clip_a) and ffprobe_has_audio(clip_b)

    parts: List[str] = []
    v_labels: List[str] = []
    a_labels: List[str] = []

    if tail_start > _MIN_FADE_SECONDS:
        v_labels.append("[v1]")
        parts.append(
            f"[0:v]trim=start=0:end={tail_start:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},format=yuv420p,"
            f"setsar=1[v1]"
        )
        if has_audio:
            a_labels.append("[a1]")
            parts.append(
                f"[0:a]atrim=start=0:end={tail_start:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000[a1]"
            )

    if transition is not None:
        parts.append(
            f"[0:v]trim=start={tail_start:.3f}:end={dur_a:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},format=yuv420p,"
            f"setsar=1[vta]"
        )
        parts.append(
            f"[2:v]trim=start=0:end={window:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},"
            f"format=yuva420p,setsar=1[vt]"
        )
        parts.append(
            "[vta][vt]overlay=0:0:format=auto,"
            f"fps={OUTPUT_FPS},format=yuv420p,setsar=1[vw]"
        )
    else:
        parts.append(
            f"[0:v]trim=start={tail_start:.3f}:end={dur_a:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},format=yuv420p,"
            f"setsar=1[vta]"
        )
        parts.append(
            f"[1:v]trim=start=0:end={window:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},format=yuv420p,"
            f"setsar=1[vhb]"
        )
        parts.append(
            f"[vta][vhb]xfade=transition=fade:duration={window:.3f}:offset=0,"
            f"fps={OUTPUT_FPS},format=yuv420p,setsar=1[vw]"
        )
    v_labels.append("[vw]")

    if has_audio:
        parts.append(
            f"[0:a]atrim=start={tail_start:.3f}:end={dur_a:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000[ata]"
        )
        parts.append(
            f"[1:a]atrim=start=0:end={window:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000[bha]"
        )
        parts.append(f"[ata][bha]acrossfade=d={window:.3f}[aw]")
        a_labels.append("[aw]")

    if dur_b - window > _MIN_FADE_SECONDS:
        v_labels.append("[v3]")
        parts.append(
            f"[1:v]trim=start={window:.3f}:end={dur_b:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:flags=lanczos,fps={OUTPUT_FPS},format=yuv420p,"
            f"setsar=1[v3]"
        )
        if has_audio:
            a_labels.append("[a3]")
            parts.append(
                f"[1:a]atrim=start={window:.3f}:end={dur_b:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000[a3]"
            )

    final_v = "[vout]" if len(v_labels) > 1 else v_labels[0]
    if len(v_labels) > 1:
        parts.append(f"{''.join(v_labels)}concat=n={len(v_labels)}:v=1:a=0[vout]")

    map_args = ["-map", final_v]
    if has_audio:
        final_a = "[aout]" if len(a_labels) > 1 else a_labels[0]
        if len(a_labels) > 1:
            parts.append(f"{''.join(a_labels)}concat=n={len(a_labels)}:v=0:a=1[aout]")
        map_args += ["-map", final_a]
    else:
        map_args += ["-an"]

    command = ["ffmpeg", "-y"]
    command += ["-i", str(clip_a), "-i", str(clip_b)]
    if transition is not None:
        command += ["-i", str(transition)]
    command += ["-filter_complex", ";".join(parts), *map_args, *_VIDEO_ENCODE_ARGS]
    if has_audio:
        command += _AUDIO_ENCODE_ARGS
    command += ["-movflags", "+faststart", str(output_path)]
    return command, has_audio


def overlay_transition_mp4(
    clip_a: Path, clip_b: Path, transition_mp4: Path, output_path: Path
) -> bool:
    """Apply a transition MP4 between two clips; True on success.

    Validates the transition file (duration <= MAX_TRANSITION_FILE_SECONDS, has a
    video stream, size <= MAX_TRANSITION_FILE_MB). When the file carries an alpha
    channel it is truly overlaid at the boundary; otherwise the render degrades
    to an xfade fade. Never raises; partial output is removed on failure.
    """
    clip_a = Path(clip_a)
    clip_b = Path(clip_b)
    transition_mp4 = Path(transition_mp4)
    output_path = Path(output_path)
    try:
        if not clip_a.is_file() or not clip_b.is_file() or not transition_mp4.is_file():
            return False
        try:
            if transition_mp4.stat().st_size > MAX_TRANSITION_FILE_MB * 1024 * 1024:
                logger.warning(
                    "Transition file too large (%d bytes): %s",
                    transition_mp4.stat().st_size,
                    transition_mp4,
                )
                return False
        except OSError:
            return False
        try:
            trans_duration = ffprobe_duration(transition_mp4)
            dur_a = ffprobe_duration(clip_a)
            dur_b = ffprobe_duration(clip_b)
            width, height = ffprobe_video_size(clip_a)
        except RuntimeError:
            return False
        if trans_duration <= 0 or trans_duration > MAX_TRANSITION_FILE_SECONDS:
            return False
        if _probe_video_stream_info(transition_mp4) is None:
            return False

        window = min(trans_duration, dur_a, dur_b)
        if window < _MIN_FADE_SECONDS:
            return False

        use_overlay = _transition_has_alpha(transition_mp4)
        transition_input = transition_mp4 if use_overlay else None

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command, _ = _build_pair_render_command(
            clip_a,
            clip_b,
            transition_input,
            dur_a,
            dur_b,
            window,
            width,
            height,
            output_path,
        )
        result = run_ffmpeg_command(command, timeout=1800)
        if result.returncode != 0:
            logger.error(
                "overlay_transition_mp4 render failed: %s", result.stderr[-2000:]
            )
            output_path.unlink(missing_ok=True)
            return False
        if not output_path.is_file():
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - contract is bool, never throw
        logger.error("overlay_transition_mp4 failed: %s", exc)
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# --- orchestrator -----------------------------------------------------------


def apply_transitions_between_clips(
    paths: List[Path],
    spec: str,
    output_dir: Path,
    *,
    hook_path: Optional[Path] = None,
) -> Path:
    """Stitch N clips into one video applying spec between consecutive clips.

    - xfade:<name>: delegates to merge_clips_with_transition (one ffmpeg pass).
    - file:<stem>: resolves the transition MP4 and overlays it pair by pair;
      any pair that fails falls back to xfade fade, then to hard concat.
    - none / unknown: hard concat of all clips.
    Final output lands in output_dir as merged_transition_<uuid12>.mp4.
    """
    clip_paths = [Path(p) for p in paths]
    if hook_path is not None:
        hook = Path(hook_path)
        if not hook.is_file():
            raise ValueError(f"Hook file does not exist: {hook}")
        clip_paths.insert(0, hook)
    if len(clip_paths) < 2:
        raise ValueError("apply_transitions_between_clips requires at least 2 clips")
    for path in clip_paths:
        if not path.is_file():
            raise ValueError(f"Clip file does not exist: {path}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"merged_transition_{uuid.uuid4().hex[:12]}.mp4"

    normalized = normalize_transition_spec(spec)
    if hook_path is not None and normalized == "none":
        normalized = "xfade:fade"
    if transition_kind(normalized) != "file":
        merged = merge_clips_with_transition(
            clip_paths,
            normalized,
            DEFAULT_COMPOSITION_FADE_SECONDS if hook_path is not None else None,
        )
        _move_to(merged, final_path)
        _discard_intermediate(merged)
        return final_path

    transition_file = resolve_transition_file(normalized)
    work_dir = output_dir / f"transition_work_{uuid.uuid4().hex[:12]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        current = clip_paths[0]
        for index in range(1, len(clip_paths)):
            next_clip = clip_paths[index]
            pair_output = work_dir / f"pair_{index:02d}_{uuid.uuid4().hex[:8]}.mp4"
            merged: Optional[Path] = None
            if transition_file is not None:
                if overlay_transition_mp4(
                    current, next_clip, transition_file, pair_output
                ):
                    merged = pair_output
            if merged is None:
                try:
                    merged = merge_clips_with_transition(
                        [current, next_clip], "xfade:fade"
                    )
                except (RuntimeError, ValueError):
                    merged = merge_clips_with_transition(
                        [current, next_clip], "none"
                    )
            consumed = current
            current = merged
            if consumed not in clip_paths:
                _discard_intermediate(consumed)
        _move_to(current, final_path)
        _discard_intermediate(current)
        return final_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
