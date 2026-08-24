"""
Transition spec grammar and validation helpers (pure, no heavy IO).

Single source of truth for the transition spec grammar:
    "none"            -> no transition (hard cut)
    "xfade:<name>"    -> built-in ffmpeg xfade transition by name
    "file:<stem>"     -> transition MP4 file in the transitions directory
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

XFADE_ALLOWLIST = [
    "fade",
    "dissolve",
    "fadeblack",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "wipeleft",
    "wiperight",
    "circleopen",
    "pixelize",
    "zoomin",
]

DEFAULT_TRANSITION = "none"
DEFAULT_FADE_SECONDS = 0.35
MAX_FADE_SECONDS = 0.5
MAX_TRANSITION_FILE_SECONDS = 1.5
MAX_TRANSITION_FILE_MB = 20

_MIN_FADE_SECONDS = 0.06
_SAFE_STEM_RE = re.compile(r"^[a-z0-9_-]+$")


def normalize_transition_spec(spec: str) -> str:
    """Validate and normalize a transition spec; invalid/unknown -> "none"."""
    if not spec:
        return DEFAULT_TRANSITION
    value = str(spec).strip().lower()
    if value in ("", "none"):
        return DEFAULT_TRANSITION
    prefix, sep, tail = value.partition(":")
    if not sep:
        return DEFAULT_TRANSITION
    if prefix == "xfade" and tail and tail in XFADE_ALLOWLIST:
        return f"xfade:{tail}"
    if prefix == "file" and tail and _SAFE_STEM_RE.match(tail):
        return f"file:{tail}"
    return DEFAULT_TRANSITION


def transition_kind(spec: str) -> str:
    """Return "none", "xfade", or "file" for the normalized spec."""
    return normalize_transition_spec(spec).partition(":")[0]


def xfade_name(spec: str) -> Optional[str]:
    """Return the validated xfade transition name, or None if not an xfade spec."""
    normalized = normalize_transition_spec(spec)
    if normalized.startswith("xfade:"):
        return normalized.partition(":")[2]
    return None


def file_stem(spec: str) -> Optional[str]:
    """Return the validated transition file stem, or None if not a file spec."""
    normalized = normalize_transition_spec(spec)
    if normalized.startswith("file:"):
        return normalized.partition(":")[2]
    return None


def builtin_transition_names() -> List[str]:
    """Return a copy of the allowed xfade transition names."""
    return list(XFADE_ALLOWLIST)


def transitions_dir() -> Path:
    """Absolute path to the bundled transition MP4 directory (backend/transitions)."""
    return Path(__file__).resolve().parent.parent / "transitions"


def resolve_transition_file(
    spec: str, base_dir: Optional[Path] = None
) -> Optional[Path]:
    """Resolve a "file:<stem>" spec to an existing transition MP4 path.

    Returns None for non-file specs or when no file matches at base_dir
    (defaults to the bundled transitions directory).
    """
    stem = file_stem(spec)
    if stem is None:
        return None
    directory = Path(base_dir) if base_dir is not None else transitions_dir()
    candidate = directory / f"{stem}.mp4"
    return candidate if candidate.is_file() else None


def clamp_fade(seconds: float, min_pair_duration: float) -> float:
    """Clamp a crossfade duration into the valid render envelope.

    Valid range is [0.06, MAX_FADE_SECONDS] and must never exceed half of the
    shortest clip in the pair. When the pair is too short to host the minimum
    fade, 0.0 is returned (meaning: no crossfade is possible).
    """
    if seconds is None or seconds <= 0 or min_pair_duration is None or min_pair_duration <= 0:
        return 0.0
    upper = min(MAX_FADE_SECONDS, float(min_pair_duration) * 0.5)
    if upper < _MIN_FADE_SECONDS:
        return 0.0
    return max(_MIN_FADE_SECONDS, min(float(seconds), upper))
