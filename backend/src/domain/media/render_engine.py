"""
Editable Composition Architecture - RenderEngine and Filter Graph Builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import subprocess
from typing import Callable, List, Optional, Union
import uuid

from ...shared.errors import RenderError
from .composition import (
    Composition,
    ReframeMode,
    RenderIntent,
    SegmentSpec,
)

logger = logging.getLogger(__name__)


def _escape_filter_path(path: Union[Path, str]) -> str:
    """Escape file path for ffmpeg filter arguments."""
    return (
        str(path)
        .replace("\\", "/")
        .replace(":", "\\\\:")
        .replace("'", "\\'")
        .replace(" ", "\\ ")
    )


def _ffprobe_duration_safe(path: Path) -> float:
    """Safely get duration of a media file via ffprobe."""
    try:
        from ...video_utils import ffprobe_duration
        return ffprobe_duration(path)
    except Exception:
        try:
            res = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return max(0.0, float(res.stdout.strip()))
        except Exception:
            return 0.0


@dataclass
class FilterGraphResult:
    command: List[str]
    inputs: List[Path]
    filter_complex: str
    cleanup_files: List[Path] = field(default_factory=list)
    output_path: Optional[Path] = None


def _format_atempo_filter(rate: float) -> str:
    """Generate atempo filter chain for rates outside 0.5 - 2.0 range."""
    if rate <= 0:
        return "atempo=1.0"
    filters = []
    current = rate
    while current < 0.5:
        filters.append("atempo=0.5")
        current /= 0.5
    while current > 2.0:
        filters.append("atempo=2.0")
        current /= 2.0
    filters.append(f"atempo={current:.4f}")
    return ",".join(filters)


def build_filter_graph(
    composition: Composition,
    source_path: Path,
    intent: Union[RenderIntent, str] = RenderIntent.export,
    output_path: Optional[Path] = None,
) -> FilterGraphResult:
    """
    Builds parameterized ffmpeg filter graph and command for a composition.

    Supports:
    - Framing/reframe: crop box (w:h:x:y), zoompan, or tracking center crop.
    - Speed: setpts and atempo if rate != 1.0.
    - Audio volume/gain: volume={gain_db}dB.
    - Captions / ASS subtitle overlay.
    - B-roll inserts (overlay at at_time for duration).
    - SFX mixing (amix / adelay).
    - Soundtrack overlay with volume.
    - Presets / scaling: preview (fast preset/crf 28) vs export (slow preset/crf 18).
    """
    if isinstance(intent, str):
        try:
            intent = RenderIntent(intent)
        except ValueError:
            intent = RenderIntent.export

    is_preview = intent == RenderIntent.preview

    # Output dimensions based on format and intent
    if is_preview:
        target_w, target_h = 540, 960
    else:
        target_w, target_h = 1080, 1920

    inputs: List[Path] = [source_path]
    cleanup_files: List[Path] = []
    filter_parts: List[str] = []

    segments = composition.segments
    if not segments:
        raise ValueError("Composition must contain at least one segment")

    # 1. Process Video and Audio for each Segment
    seg_v_labels: List[str] = []
    seg_a_labels: List[str] = []

    for idx, seg in enumerate(segments):
        s_start = max(0.0, seg.source_start)
        s_end = max(s_start + 0.05, seg.source_end)
        seg_duration = s_end - s_start

        # --- Video Pipeline for segment ---
        v_chain: List[str] = [
            f"[0:v]trim=start={s_start:.3f}:end={s_end:.3f}",
            "setpts=PTS-STARTPTS",
        ]

        # Speed adjustment
        if seg.speed.rate != 1.0 and seg.speed.rate > 0:
            v_chain.append(f"setpts=PTS-STARTPTS/{seg.speed.rate:.4f}")

        # Framing / Reframe
        reframe = seg.reframe
        if reframe.box:
            # Crop box with relative coordinates (0.0 - 1.0)
            box = reframe.box
            bx = max(0.0, min(1.0, float(box.get("x", 0.0))))
            by = max(0.0, min(1.0, float(box.get("y", 0.0))))
            bw = max(0.05, min(1.0, float(box.get("w", 1.0))))
            bh = max(0.05, min(1.0, float(box.get("h", 1.0))))
            v_chain.append(
                f"crop=w=iw*{bw:.4f}:h=ih*{bh:.4f}:x=iw*{bx:.4f}:y=ih*{by:.4f}"
            )
            v_chain.append(f"scale={target_w}:{target_h}:flags=lanczos,setsar=1")
        elif reframe.mode == ReframeMode.zoompan:
            v_chain.append(
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1"
            )
            v_chain.append(
                f"zoompan=z='min(zoom+0.0015,1.15)':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*0.35':d=1:s={target_w}x{target_h}:fps=30"
            )
        else:
            # Default track / center vertical crop
            v_chain.append(
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1"
            )

        v_label = f"v_seg_{idx}"
        filter_parts.append(f"{','.join(v_chain)}[{v_label}]")
        seg_v_labels.append(f"[{v_label}]")

        # --- Audio Pipeline for segment ---
        if seg.audio.take_source:
            a_chain: List[str] = [
                f"[0:a]atrim=start={s_start:.3f}:end={s_end:.3f}",
                "asetpts=PTS-STARTPTS",
            ]
            if seg.speed.rate != 1.0 and seg.speed.rate > 0:
                a_chain.append(_format_atempo_filter(seg.speed.rate))
            if seg.audio.gain_db != 0.0:
                a_chain.append(f"volume={seg.audio.gain_db:.2f}dB")
            a_label = f"a_seg_{idx}"
            filter_parts.append(f"{','.join(a_chain)}[{a_label}]")
            seg_a_labels.append(f"[{a_label}]")
        else:
            # Silence
            effective_dur = seg_duration / (seg.speed.rate if seg.speed.rate > 0 else 1.0)
            a_label = f"a_seg_{idx}"
            filter_parts.append(
                f"aevalsrc=0:d={effective_dur:.3f}[{a_label}]"
            )
            seg_a_labels.append(f"[{a_label}]")

    # 2. Concat Segments
    if len(segments) == 1:
        cur_v = seg_v_labels[0]
        cur_a = seg_a_labels[0]
    else:
        concat_v_inputs = "".join(seg_v_labels)
        concat_a_inputs = "".join(seg_a_labels)
        filter_parts.append(
            f"{concat_v_inputs}concat=n={len(segments)}:v=1:a=0[v_base]"
        )
        filter_parts.append(
            f"{concat_a_inputs}concat=n={len(segments)}:v=0:a=1[a_base]"
        )
        cur_v = "[v_base]"
        cur_a = "[a_base]"

    # 3. B-Roll Inserts
    for b_idx, broll in enumerate(composition.broll_inserts):
        if broll.asset_path and Path(broll.asset_path).is_file():
            broll_path = Path(broll.asset_path)
            inputs.append(broll_path)
            inp_num = len(inputs) - 1
            broll_v_label = f"vbroll_{b_idx}"
            filter_parts.append(
                f"[{inp_num}:v]trim=start=0:end={broll.duration:.3f},setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1[{broll_v_label}]"
            )
            out_v = f"v_after_broll_{b_idx}"
            b_start = max(0.0, broll.at_time)
            b_end = b_start + max(0.05, broll.duration)
            filter_parts.append(
                f"{cur_v}[{broll_v_label}]overlay=enable='between(t,{b_start:.3f},{b_end:.3f})':eof_action=pass[{out_v}]"
            )
            cur_v = f"[{out_v}]"

    # 4. Captions / Subtitles Overlay
    first_seg_caption = segments[0].caption if segments else None
    if first_seg_caption:
        try:
            from ...video_utils import ass_fonts_dir, build_assemblyai_ass_subtitles

            parent_dir = output_path.parent if output_path else source_path.parent
            temp_ass = parent_dir / f"captions_{uuid.uuid4().hex[:8]}.ass"
            has_ass = False
            tot_dur = sum(s.source_end - s.source_start for s in segments)
            if first_seg_caption.text_override:
                words = first_seg_caption.text_override.split()
                if words:
                    word_dur = tot_dur / len(words)
                    caption_words = [
                        {"text": w, "start": i * word_dur, "end": (i + 1) * word_dur}
                        for i, w in enumerate(words)
                    ]
                    has_ass = build_assemblyai_ass_subtitles(
                        source_path,
                        clip_start=0.0,
                        clip_end=tot_dur,
                        video_width=target_w,
                        video_height=target_h,
                        output_ass_path=temp_ass,
                        font_family=first_seg_caption.font_family,
                        font_size=first_seg_caption.font_size,
                        font_color=first_seg_caption.font_color,
                        caption_template=first_seg_caption.template or "default",
                        caption_words=caption_words,
                        highlight_words=first_seg_caption.highlight_words or None,
                    )
            else:
                has_ass = build_assemblyai_ass_subtitles(
                    source_path,
                    clip_start=segments[0].source_start,
                    clip_end=segments[-1].source_end,
                    video_width=target_w,
                    video_height=target_h,
                    output_ass_path=temp_ass,
                    font_family=first_seg_caption.font_family,
                    font_size=first_seg_caption.font_size,
                    font_color=first_seg_caption.font_color,
                    caption_template=first_seg_caption.template or "default",
                    highlight_words=first_seg_caption.highlight_words or None,
                )

            if has_ass and temp_ass.is_file():
                cleanup_files.append(temp_ass)
                fonts_dir = ass_fonts_dir(first_seg_caption.font_family)
                sub_filter = f"subtitles=filename='{_escape_filter_path(temp_ass)}'"
                if fonts_dir:
                    sub_filter += f":fontsdir='{_escape_filter_path(fonts_dir)}'"
                filter_parts.append(f"{cur_v}{sub_filter}[v_sub]")
                cur_v = "[v_sub]"
        except Exception as exc:
            logger.debug("Caption overlay generation skipped: %s", exc)

    # 5. Audio Mixing (SFX & Soundtrack)
    audio_mix_inputs = [cur_a]

    # SFX
    for s_idx, sfx in enumerate(composition.sfx):
        if sfx.asset_path and Path(sfx.asset_path).is_file():
            sfx_path = Path(sfx.asset_path)
            inputs.append(sfx_path)
            inp_num = len(inputs) - 1
            delay_ms = int(max(0.0, sfx.at_time) * 1000)
            sfx_a_label = f"sfx_a_{s_idx}"
            sfx_gain = f",volume={sfx.gain_db:.2f}dB" if sfx.gain_db != 0.0 else ""
            filter_parts.append(
                f"[{inp_num}:a]atrim=duration={sfx.duration:.3f},asetpts=PTS-STARTPTS{sfx_gain},"
                f"adelay={delay_ms}|{delay_ms}:all=1[{sfx_a_label}]"
            )
            audio_mix_inputs.append(f"[{sfx_a_label}]")

    # Soundtrack
    if composition.soundtrack and composition.soundtrack.asset_path and Path(composition.soundtrack.asset_path).is_file():
        st_path = Path(composition.soundtrack.asset_path)
        inputs.append(st_path)
        inp_num = len(inputs) - 1
        tot_dur = sum(s.source_end - s.source_start for s in segments)
        st_vol = composition.soundtrack.volume
        filter_parts.append(
            f"[{inp_num}:a]atrim=duration={tot_dur:.3f},asetpts=PTS-STARTPTS,volume={st_vol:.3f}[soundtrack_a]"
        )
        audio_mix_inputs.append("[soundtrack_a]")

    if len(audio_mix_inputs) > 1:
        concat_inputs = "".join(audio_mix_inputs)
        filter_parts.append(
            f"{concat_inputs}amix=inputs={len(audio_mix_inputs)}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a_final]"
        )
        final_a = "[a_final]"
    else:
        final_a = cur_a

    final_filter_complex = ";".join(filter_parts)

    # 6. Build ffmpeg command
    cmd: List[str] = ["ffmpeg", "-y"]
    for inp in inputs:
        cmd.extend(["-i", str(inp)])

    cmd.extend(["-filter_complex", final_filter_complex])
    cmd.extend(["-map", cur_v, "-map", final_a])

    if is_preview:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "18",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "256k",
            "-movflags", "+faststart",
        ])

    if output_path:
        cmd.append(str(output_path))

    return FilterGraphResult(
        command=cmd,
        inputs=inputs,
        filter_complex=final_filter_complex,
        cleanup_files=cleanup_files,
        output_path=output_path,
    )


class RenderEngine:
    """Executes ffmpeg render pipelines for compositions with atomic swap semantics."""

    def render(
        self,
        composition: Composition,
        source_path: Path,
        output_dir: Path,
        intent: Union[RenderIntent, str] = RenderIntent.export,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Path:
        """
        Renders composition to output_dir with atomic swap:
        Writes to temp_{uuid}.mp4, verifies duration > 0, moves to final destination.
        If render fails or output is invalid, deletes temp file and leaves existing artifacts untouched.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not source_path.is_file():
            raise RenderError(f"Source video file not found: {source_path}")

        temp_id = uuid.uuid4().hex
        temp_file = output_dir / f"temp_{temp_id}.mp4"
        final_file = output_dir / f"clip_{temp_id[:12]}.mp4"

        def _report(pct: int, msg: str) -> None:
            if progress_callback:
                try:
                    progress_callback(pct, msg)
                except Exception:
                    pass

        _report(10, "Building render filter graph...")
        graph_result = build_filter_graph(
            composition=composition,
            source_path=source_path,
            intent=intent,
            output_path=temp_file,
        )

        try:
            _report(30, "Rendering with ffmpeg...")
            res = subprocess.run(
                graph_result.command,
                capture_output=True,
                text=True,
                timeout=1800,
            )

            if res.returncode != 0:
                logger.error("ffmpeg render failed: %s\n%s", " ".join(graph_result.command), res.stderr[-4000:])
                raise RenderError(f"ffmpeg render failed (code {res.returncode}): {res.stderr[-500:]}")

            # Verify output file exists and duration > 0
            if not temp_file.is_file() or temp_file.stat().st_size == 0:
                raise RenderError("Render output file is missing or empty")

            duration = _ffprobe_duration_safe(temp_file)
            if duration <= 0:
                raise RenderError(f"Rendered clip has invalid duration: {duration:.2f}s")

            # Atomic swap: rename/move to final destination
            _report(90, "Finalizing clip...")
            if temp_file != final_file:
                if final_file.exists():
                    final_file.unlink(missing_ok=True)
                temp_file.replace(final_file)

            _report(100, "Render complete")
            return final_file

        except Exception as exc:
            # Clean up temp file on failure; leave existing artifacts untouched
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            if not isinstance(exc, RenderError):
                raise RenderError(f"Render pipeline failed: {exc}") from exc
            raise
        finally:
            # Clean up temporary ASS subtitle files
            for cleanup_path in graph_result.cleanup_files:
                try:
                    cleanup_path.unlink(missing_ok=True)
                except Exception:
                    pass

    @classmethod
    def render_composition(
        cls,
        composition: Composition,
        source_path: Path,
        output_dir: Path,
        intent: Union[RenderIntent, str] = RenderIntent.export,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Path:
        return cls().render(composition, source_path, output_dir, intent, progress_callback)
