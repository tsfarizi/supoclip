from pathlib import Path
import pytest

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
)
from src.domain.media.render_engine import RenderEngine, build_filter_graph
from src.shared.errors import RenderError


def test_build_filter_graph_basic_export():
    comp = Composition(
        schema_version=1,
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        output=OutputSpec(format="vertical", preset="tiktok"),
        segments=[
            SegmentSpec(
                id="seg-1",
                source_start=1.0,
                source_end=5.0,
                reframe=ReframeSpec(mode=ReframeMode.crop, box={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}),
                speed=SpeedSpec(rate=1.2),
                audio=AudioSpec(take_source=True, gain_db=3.0),
                caption=CaptionSpec(),
            )
        ],
        broll_inserts=[],
        sfx=[],
    )

    res = build_filter_graph(comp, Path("dummy.mp4"), intent=RenderIntent.export)
    cmd_str = " ".join(res.command)

    assert "crop=" in cmd_str
    assert "scale=1080:1920" in cmd_str
    assert "setpts=PTS-STARTPTS/1.2000" in cmd_str
    assert "atempo=1.2000" in cmd_str
    assert "volume=3.00dB" in cmd_str
    assert "-preset slow" in cmd_str
    assert "-crf 18" in cmd_str


def test_build_filter_graph_preview_preset():
    comp = Composition(
        schema_version=1,
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        output=OutputSpec(format="vertical", preset="tiktok"),
        segments=[
            SegmentSpec(
                id="seg-1",
                source_start=0.0,
                source_end=10.0,
                reframe=ReframeSpec(mode=ReframeMode.track),
                speed=SpeedSpec(rate=1.0),
                audio=AudioSpec(take_source=True, gain_db=0.0),
                caption=CaptionSpec(),
            )
        ],
    )

    res = build_filter_graph(comp, Path("dummy.mp4"), intent=RenderIntent.preview)
    cmd_str = " ".join(res.command)

    assert "scale=540:960" in cmd_str
    assert "-preset ultrafast" in cmd_str
    assert "-crf 28" in cmd_str


def test_build_filter_graph_with_broll_sfx_soundtrack(tmp_path):
    broll_file = tmp_path / "broll.mp4"
    broll_file.touch()
    sfx_file = tmp_path / "whoosh.mp3"
    sfx_file.touch()
    soundtrack_file = tmp_path / "bg.mp3"
    soundtrack_file.touch()

    comp = Composition(
        schema_version=1,
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        output=OutputSpec(format="vertical", preset="tiktok"),
        segments=[
            SegmentSpec(
                id="seg-1",
                source_start=0.0,
                source_end=10.0,
                reframe=ReframeSpec(mode=ReframeMode.track),
                speed=SpeedSpec(rate=1.0),
                audio=AudioSpec(take_source=True, gain_db=0.0),
                caption=CaptionSpec(),
            )
        ],
        broll_inserts=[
            BrollInsertSpec(
                id="broll-1",
                at_time=2.0,
                duration=3.0,
                search_term="ocean",
                asset_path=str(broll_file),
            )
        ],
        sfx=[
            SoundFxSpec(
                id="sfx-1",
                at_time=1.5,
                duration=1.0,
                query="whoosh",
                gain_db=-3.0,
                asset_path=str(sfx_file),
            )
        ],
        soundtrack=SoundtrackSpec(
            asset_path=str(soundtrack_file),
            volume=0.4,
        ),
    )

    res = build_filter_graph(comp, Path("dummy.mp4"), intent=RenderIntent.export)
    cmd_str = " ".join(res.command)

    assert "overlay=" in cmd_str
    assert "between(t,2.000,5.000)" in cmd_str
    assert "adelay=1500|1500:all=1" in cmd_str
    assert "amix=inputs=3" in cmd_str
    assert "volume=0.400" in cmd_str


def test_render_engine_nonexistent_source(tmp_path):
    comp = Composition(
        schema_version=1,
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        output=OutputSpec(format="vertical"),
        segments=[
            SegmentSpec(
                id="seg-1",
                source_start=0.0,
                source_end=5.0,
            )
        ],
    )
    engine = RenderEngine()
    with pytest.raises(RenderError, match="Source video file not found"):
        engine.render(comp, tmp_path / "nonexistent.mp4", tmp_path / "out")


def test_build_filter_graph_empty_segments_raises():
    comp = Composition(
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        segments=[],
    )
    with pytest.raises(ValueError, match="Composition must contain at least one segment"):
        build_filter_graph(comp, Path("dummy.mp4"))


def test_build_filter_graph_multi_segment_concat():
    comp = Composition(
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        segments=[
            SegmentSpec(id="seg-1", source_start=0.0, source_end=5.0),
            SegmentSpec(id="seg-2", source_start=10.0, source_end=15.0),
        ],
    )
    res = build_filter_graph(comp, Path("dummy.mp4"), intent=RenderIntent.export)
    cmd_str = " ".join(res.command)
    assert "concat=n=2:v=1:a=0[v_base]" in cmd_str
    assert "concat=n=2:v=0:a=1[a_base]" in cmd_str


def test_build_filter_graph_zoompan_mode():
    comp = Composition(
        source_asset_ref=SourceAssetRef(source_id="src-1"),
        segments=[
            SegmentSpec(
                id="seg-1",
                source_start=0.0,
                source_end=5.0,
                reframe=ReframeSpec(mode=ReframeMode.zoompan),
            ),
        ],
    )
    res = build_filter_graph(comp, Path("dummy.mp4"), intent=RenderIntent.export)
    cmd_str = " ".join(res.command)
    assert "zoompan=" in cmd_str

