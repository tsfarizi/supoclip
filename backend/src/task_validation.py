"""Pure input-normalization helpers for task fields.

Single source of truth for the bounds and defaults applied at the API route,
service, and repository boundaries. Every function is pure: no I/O, no config
reads  -  callers pass the effective default explicitly (e.g. the runtime
config's default processing mode). Defaults match the historical route
behavior exactly and must not be changed without a contract change.
"""

import re
from typing import Any, Optional

from .video_utils import VALID_OUTPUT_FORMATS

VALID_PROCESSING_MODES = ("fast", "balanced", "quality")

FONT_SIZE_MIN = 12
FONT_SIZE_MAX = 72
DEFAULT_FONT_SIZE = 24

DEFAULT_FONT_COLOR = "#FFFFFF"
DEFAULT_OUTPUT_FORMAT = "vertical"

SOUND_EFFECTS_COUNT_MIN = 0
SOUND_EFFECTS_COUNT_MAX = 5

_HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_font_family(value: Any) -> Optional[str]:
    """Strip a font-family name; blank or non-string input becomes None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_font_size(value: Any, default: int = DEFAULT_FONT_SIZE) -> Optional[int]:
    """Clamp a font size to 12-72; None/blank stays None, unparseable becomes default."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, parsed))


def normalize_font_color(
    value: Any, default: str = DEFAULT_FONT_COLOR
) -> Optional[str]:
    """Accept a 6-digit hex color (normalized to uppercase); anything else is default."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and _HEX_COLOR_PATTERN.match(value):
        return value.upper()
    return default


def clamp_sound_effects_count(value: Any, default: int = 0) -> int:
    """Clamp a sound-effects count to 0-5; unparseable input becomes default."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(SOUND_EFFECTS_COUNT_MIN, min(SOUND_EFFECTS_COUNT_MAX, parsed))


def normalize_processing_mode(value: Any, default: str) -> str:
    """Accept one of fast/balanced/quality; anything else becomes the caller's default."""
    if value in VALID_PROCESSING_MODES:
        return value
    return default


def normalize_output_format(
    value: Any, default: str = DEFAULT_OUTPUT_FORMAT
) -> str:
    """Accept one of VALID_OUTPUT_FORMATS; anything else becomes default."""
    if value in VALID_OUTPUT_FORMATS:
        return value
    return default


def normalize_boolean(value: Any, default: bool) -> bool:
    """Pass a real bool through; any other type becomes default."""
    if isinstance(value, bool):
        return value
    return default