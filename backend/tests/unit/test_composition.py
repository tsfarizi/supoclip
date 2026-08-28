import json
import pytest
from pydantic import ValidationError

from src.domain.media.composition import (
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


def test_render_intent_and_reframe_mode():
    assert RenderIntent.preview.value == "preview"
    assert RenderIntent.export.value == "export"
    assert ReframeMode.crop.value == "crop"
    assert ReframeMode.zoompan.value == "zoompan"
    assert ReframeMode.track.value == "track"


def test_reframe_spec_defaults_and_box_validation():
    ref = ReframeSpec()
    assert ref.mode == ReframeMode.track
    assert ref.box is None
    assert ref.track_target is None

    valid = ReframeSpec(mode=ReframeMode.crop, box={"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.8})
    assert valid.box["w"] == 0.5

    with pytest.raises(ValidationError):
        ReframeSpec(box={"x": 1.5, "y": 0.0, "w": 0.5, "h": 0.5})

    with pytest.raises(ValidationError):
        ReframeSpec(box={"x": 0.0, "y": 0.0, "w": 0.5})  # missing h


def test_composition_serialization_round_trip():
    comp = Composition(
        schema_version=1,
        source_asset_ref=SourceAssetRef(
            source_id="src-123",
            source_type="youtube",
            source_url="https://youtube.com/watch?v=12345",
            source_identity="youtube:12345",
        ),
        output=OutputSpec(format="vertical", preset="tiktok"),
        segments=[
            SegmentSpec(
                id="seg-1",
                source_start=0.0,
                source_end=15.5,
                reframe=ReframeSpec(mode=ReframeMode.track, track_target="speaker_1"),
                speed=SpeedSpec(rate=1.0, ramp=None),
                audio=AudioSpec(take_source=True, gain_db=0.0, ducking={"amount_db": -6.0}),
                caption=CaptionSpec(
                    text_override="Test subtitle",
                    font_family="TikTokSans-Regular",
                    font_size=24,
                    font_color="#FFFFFF",
                    template="default",
                    position="bottom",
                    highlight_words=["Test"],
                ),
            )
        ],
        broll_inserts=[
            BrollInsertSpec(
                id="broll-1",
                at_time=5.0,
                duration=3.0,
                search_term="sunset",
                asset_path="/tmp/sunset.mp4",
                reframe=ReframeSpec(mode=ReframeMode.crop),
            )
        ],
        sfx=[
            SoundFxSpec(
                id="sfx-1",
                at_time=2.0,
                duration=1.0,
                query="whoosh",
                intensity="moderate",
                gain_db=-2.0,
                placement="main",
                asset_path="/tmp/whoosh.mp3",
            )
        ],
        soundtrack=SoundtrackSpec(asset_path="/tmp/bg.mp3", volume=0.3),
        transitions=[
            TransitionSpec(between=("seg-1", "seg-2"), type="xfade:dissolve", duration=0.5)
        ],
    )

    # Test to_dict / from_dict
    comp_dict = comp.to_dict()
    assert isinstance(comp_dict, dict)
    assert comp_dict["schema_version"] == 1
    assert comp_dict["segments"][0]["id"] == "seg-1"
    assert comp_dict["transitions"][0]["between"] == ("seg-1", "seg-2")

    comp_from_dict = Composition.from_dict(comp_dict)
    assert comp_from_dict.segments[0].id == "seg-1"
    assert comp_from_dict.transitions[0].between == ("seg-1", "seg-2")

    # Test to_json / from_json
    json_str = comp.to_json()
    assert isinstance(json_str, str)
    comp_from_json = Composition.from_json(json_str)
    assert comp_from_json.source_asset_ref.source_id == "src-123"
    assert comp_from_json.broll_inserts[0].search_term == "sunset"
    assert comp_from_json.sfx[0].query == "whoosh"
    assert comp_from_json.soundtrack.volume == 0.3


def test_composition_validation_edge_cases():
    # Empty segments list is allowed by model, but segment spec validations
    with pytest.raises(ValidationError):
        # Missing required fields like id, source_start, source_end
        SegmentSpec.model_validate({"id": "seg-1"})

    # Box coordinates out of range
    with pytest.raises(ValidationError):
        ReframeSpec(box={"x": -0.1, "y": 0.0, "w": 0.5, "h": 0.5})

    with pytest.raises(ValidationError):
        ReframeSpec(box={"x": 0.0, "y": 1.1, "w": 0.5, "h": 0.5})

    with pytest.raises(ValidationError):
        ReframeSpec(box={"x": 0.0, "y": 0.0, "w": -0.5, "h": 0.5})


def test_composition_default_factories():
    comp = Composition(
        source_asset_ref=SourceAssetRef(source_id="src-1"),
    )
    assert comp.schema_version == 1
    assert comp.output.format == "vertical"
    assert comp.output.preset == "tiktok"
    assert comp.segments == []
    assert comp.broll_inserts == []
    assert comp.sfx == []
    assert comp.soundtrack is None
    assert comp.transitions == []

