"""
Media domain module.
"""

from .composition import (
    AudioSpec,
    BrollInsertSpec,
    CaptionSpec,
    Composition,
    OutputSpec,
    ReframeMode,
    ReframeSpec,
    RenderIntent,
    SegmentSpec,
    SoundFxSpec,
    SoundtrackSpec,
    SourceAssetRef,
    SpeedSpec,
    TransitionSpec,
)
from .composition_builder import CompositionBuilder
from .concurrency_governor import (
    acquire_render_slot,
    release_render_slot,
    render_slot_guard,
)
from .render_engine import RenderEngine, build_filter_graph
from .resolver import SourceAssetResolver, SourceUnavailableError

__all__ = [
    "RenderIntent",
    "ReframeMode",
    "ReframeSpec",
    "SpeedSpec",
    "AudioSpec",
    "CaptionSpec",
    "SegmentSpec",
    "BrollInsertSpec",
    "SoundFxSpec",
    "SoundtrackSpec",
    "TransitionSpec",
    "SourceAssetRef",
    "OutputSpec",
    "Composition",
    "CompositionBuilder",
    "RenderEngine",
    "build_filter_graph",
    "acquire_render_slot",
    "release_render_slot",
    "render_slot_guard",
    "SourceAssetResolver",
    "SourceUnavailableError",
]
