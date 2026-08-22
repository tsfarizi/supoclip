"""
Clip metadata domain contracts: per-clip AI description + hashtags.

Isolated from ai.py: owns marketing copy concerns only. Prompt versioning
and sanitization live here; virality/segment selection stays in ai.py.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

CLIP_METADATA_VERSION = "clip-metadata-v1"
CLIP_DESCRIPTION_MIN_CHARS = 80
CLIP_DESCRIPTION_MAX_CHARS = 300
CLIP_HASHTAG_MIN = 3
CLIP_HASHTAG_MAX = 8
CLIP_HASHTAG_RE = re.compile(r"^[a-z0-9_]{3,15}$")


def normalize_hashtags(raw: list[str] | None) -> list[str]:
    """Normalize raw hashtag strings into canonical '#tag' form.

    - strip leading '#', lowercase, remove non [a-z0-9_], dedup, filter 3-15 chars, cap 8.
    - Returns list of '#tag' strings.
    """
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not item:
            continue
        tag = str(item).strip().lower()
        tag = tag.lstrip("#").strip()
        # split on whitespace/comma — keep first token per raw entry
        tag = re.split(r"[\s,]+", tag)[0] if tag else ""
        tag = re.sub(r"[^a-z0-9_]", "", tag)
        if not tag or len(tag) < 3 or len(tag) > 15:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(f"#{tag}")
        if len(out) >= CLIP_HASHTAG_MAX:
            break
    return out


def sanitize_description(raw: Optional[str]) -> Optional[str]:
    """Normalize AI description: collapse whitespace, trim, enforce 80-300, strip hashtags."""
    if not raw:
        return None
    text = str(raw).strip()
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Drop wrapping quotes
    text = text.strip("\"'`\u201c\u201d\u2018\u2019").strip()
    if not text:
        return None
    if len(text) < CLIP_DESCRIPTION_MIN_CHARS:
        return None
    if len(text) > CLIP_DESCRIPTION_MAX_CHARS:
        clipped = text[: CLIP_DESCRIPTION_MAX_CHARS + 1]
        cut = clipped.rfind(" ")
        text = (clipped[:cut] if cut > 40 else text[:CLIP_DESCRIPTION_MAX_CHARS]).rstrip(".,;:- \u2013\u2014 ").strip()
    return text or None


def extract_keyword_fallback_hashtags(
    text: str, hook_title: Optional[str], key_topics: list[str] | None, limit: int = 5
) -> list[str]:
    """Deterministic fallback: derive hashtags from hook_title + key_topics + text keywords."""
    STOP = {
        "yang", "dan", "untuk", "dari", "dengan", "ini", "itu", "adalah", "akan", "tidak",
        "ada", "the", "and", "for", "with", "this", "that", "you", "your", "have", "are",
        "was", "were", "yang", "di", "ke", "pada", "dalam", "atau", "juga", "bisa", "karena",
        "jadi", "tapi", "agar", "sebuah", "sudah", "belum", "masih", "sangat", "lebih",
    }
    candidates: list[str] = []
    sources = " ".join(filter(None, [hook_title or "", " ".join(key_topics or []), text or ""]))
    for token in re.findall(r"[A-Za-z0-9_]{3,15}", sources.lower()):
        if token in STOP:
            continue
        if token not in candidates:
            candidates.append(token)
        if len(candidates) >= 10:
            break
    raw = [f"#{c}" for c in candidates[:limit]]
    return normalize_hashtags(raw)


class ClipMetadata(BaseModel):
    """Validated per-clip marketing copy."""

    clip_index: int = Field(ge=0)
    description: str = Field(min_length=CLIP_DESCRIPTION_MIN_CHARS, max_length=CLIP_DESCRIPTION_MAX_CHARS)
    hashtags: list[str] = Field(min_length=CLIP_HASHTAG_MIN, max_length=CLIP_HASHTAG_MAX)
    language: str = Field(default="id", description="id|en|auto")
    platform: Literal["tiktok", "reels", "shorts", "generic"] = "generic"
    prompt_version: str = Field(default=CLIP_METADATA_VERSION)
    fallback: bool = False

    @field_validator("description", mode="before")
    @classmethod
    def _sanitize_desc(cls, v):
        sanitized = sanitize_description(v)
        if sanitized is None:
            raise ValueError("description must be 80-300 chars after sanitization")
        return sanitized

    @field_validator("hashtags", mode="before")
    @classmethod
    def _normalize_tags(cls, v):
        if v is None:
            raise ValueError("hashtags required")
        tags = normalize_hashtags(list(v) if isinstance(v, list) else [])
        if len(tags) < CLIP_HASHTAG_MIN:
            raise ValueError(f"at least {CLIP_HASHTAG_MIN} hashtags required")
        return tags


class ClipMetadataBatch(BaseModel):
    """Batch output from LLM: one entry per segment, order preserved."""

    clips: list[ClipMetadata] = Field(min_length=1)
    batch_reasoning: Optional[str] = None
