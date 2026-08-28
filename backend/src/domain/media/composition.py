"""
Editable Composition Architecture Domain Schema (Phase 1).

Contracts:
- RenderIntent: Enum ("preview", "export")
- ReframeMode: Enum ("crop", "zoompan", "track")
- ReframeSpec: mode: ReframeMode = track, box: Optional[dict] = None (x, y, w, h floats 0.0-1.0), track_target: Optional[str] = None
- SpeedSpec: rate: float = 1.0, ramp: Optional[list] = None
- AudioSpec: take_source: bool = True, gain_db: float = 0.0, ducking: Optional[dict] = None
- CaptionSpec: text_override: Optional[str] = None, font_family: Optional[str] = None, font_size: Optional[int] = None, font_color: Optional[str] = None, template: str = "default", position: str = "bottom", highlight_words: list[str] = []
- SegmentSpec: id: str, source_start: float, source_end: float, reframe: ReframeSpec, speed: SpeedSpec, audio: AudioSpec, caption: CaptionSpec
- BrollInsertSpec: id: str, at_time: float, duration: float, search_term: str, asset_path: Optional[str] = None, reframe: Optional[ReframeSpec] = None
- SoundFxSpec: id: str, at_time: float, duration: float, query: str, intensity: str = "moderate", gain_db: float = 0.0, placement: str = "main", asset_path: Optional[str] = None
- SoundtrackSpec: asset_path: Optional[str] = None, volume: float = 0.3
- TransitionSpec: between: tuple[str, str] (seg_a, seg_b), type: str = "none", duration: float = 0.5
- SourceAssetRef: source_id: Optional[str] = None, source_type: Optional[str] = None, source_url: Optional[str] = None, source_identity: Optional[str] = None
- OutputSpec: format: str = "vertical", preset: str = "tiktok"
- Composition: schema_version: int = 1, source_asset_ref: SourceAssetRef, output: OutputSpec, segments: list[SegmentSpec], broll_inserts: list[BrollInsertSpec] = [], sfx: list[SoundFxSpec] = [], soundtrack: Optional[SoundtrackSpec] = None, transitions: list[TransitionSpec] = []
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


class RenderIntent(str, Enum):
    preview = "preview"
    export = "export"


class ReframeMode(str, Enum):
    crop = "crop"
    zoompan = "zoompan"
    track = "track"


class ReframeSpec(BaseModel):
    mode: ReframeMode = ReframeMode.track
    box: Optional[dict[str, float]] = None  # x, y, w, h floats 0.0-1.0
    track_target: Optional[str] = None

    @field_validator("box")
    @classmethod
    def validate_box(cls, v: Optional[dict[str, float]]) -> Optional[dict[str, float]]:
        if v is not None:
            required_keys = {"x", "y", "w", "h"}
            if not required_keys.issubset(v.keys()):
                raise ValueError(f"Box must contain {required_keys}")
            for k in required_keys:
                val = float(v[k])
                if not (0.0 <= val <= 1.0):
                    raise ValueError(f"Box {k} must be between 0.0 and 1.0 (got {val})")
        return v


class SpeedSpec(BaseModel):
    rate: float = 1.0
    ramp: Optional[list[Any]] = None


class AudioSpec(BaseModel):
    take_source: bool = True
    gain_db: float = 0.0
    ducking: Optional[dict[str, Any]] = None


class CaptionSpec(BaseModel):
    text_override: Optional[str] = None
    font_family: Optional[str] = None
    font_size: Optional[int] = None
    font_color: Optional[str] = None
    template: str = "default"
    position: str = "bottom"
    highlight_words: list[str] = Field(default_factory=list)


class SegmentSpec(BaseModel):
    id: str
    source_start: float
    source_end: float
    reframe: ReframeSpec = Field(default_factory=ReframeSpec)
    speed: SpeedSpec = Field(default_factory=SpeedSpec)
    audio: AudioSpec = Field(default_factory=AudioSpec)
    caption: CaptionSpec = Field(default_factory=CaptionSpec)


class BrollInsertSpec(BaseModel):
    id: str
    at_time: float
    duration: float
    search_term: str
    asset_path: Optional[str] = None
    reframe: Optional[ReframeSpec] = None


class SoundFxSpec(BaseModel):
    id: str
    at_time: float
    duration: float
    query: str
    intensity: str = "moderate"
    gain_db: float = 0.0
    placement: str = "main"
    asset_path: Optional[str] = None


class SoundtrackSpec(BaseModel):
    asset_path: Optional[str] = None
    volume: float = 0.3


class TransitionSpec(BaseModel):
    between: Tuple[str, str]  # (seg_a, seg_b)
    type: str = "none"
    duration: float = 0.5


class SourceAssetRef(BaseModel):
    source_id: Optional[str] = None
    source_type: Optional[str] = None
    source_url: Optional[str] = None
    source_identity: Optional[str] = None


class OutputSpec(BaseModel):
    format: str = "vertical"
    preset: str = "tiktok"


class Composition(BaseModel):
    schema_version: int = 1
    source_asset_ref: SourceAssetRef
    output: OutputSpec = Field(default_factory=OutputSpec)
    segments: list[SegmentSpec] = Field(default_factory=list)
    broll_inserts: list[BrollInsertSpec] = Field(default_factory=list)
    sfx: list[SoundFxSpec] = Field(default_factory=list)
    soundtrack: Optional[SoundtrackSpec] = None
    transitions: list[TransitionSpec] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert Composition to python dictionary."""
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Composition:
        """Create Composition from python dictionary."""
        return cls.model_validate(data)

    def to_json(self, **kwargs: Any) -> str:
        """Convert Composition to JSON string."""
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> Composition:
        """Create Composition from JSON string."""
        data = json.loads(json_str)
        return cls.model_validate(data)
