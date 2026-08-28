import pytest

from src.ai import HookSelection, TranscriptSegment
from src.domain.media.composition import ReframeMode, SourceAssetRef
from src.domain.media.composition_builder import CompositionBuilder


def test_composition_builder_basic():
    seg = TranscriptSegment(
        start_time="00:10",
        end_time="00:25",
        text="Hello world test segment",
        relevance_score=0.85,
    )
    source_ref = SourceAssetRef(
        source_id="src-100",
        source_type="youtube",
        source_url="https://youtube.com/watch?v=xyz",
        source_identity="youtube:xyz",
    )

    comp = CompositionBuilder.build_from_segment(
        segment=seg,
        source_ref=source_ref,
        output_format="vertical",
        font_family="TikTokSans-Regular",
        font_size=28,
        font_color="#FFFF00",
        caption_template="default",
    )

    assert comp.schema_version == 1
    assert comp.source_asset_ref.source_id == "src-100"
    assert comp.output.format == "vertical"
    assert len(comp.segments) == 1
    assert comp.segments[0].source_start == 10.0
    assert comp.segments[0].source_end == 25.0
    assert comp.segments[0].caption.font_size == 28
    assert comp.segments[0].caption.font_color == "#FFFF00"
    assert comp.segments[0].reframe.mode == ReframeMode.track


def test_composition_builder_with_hook_and_opportunities():
    seg = TranscriptSegment(
        start_time="01:00",
        end_time="01:30",
        text="Main clip text",
        relevance_score=0.9,
        hook_selection=HookSelection(
            hook_start_time="00:05",
            hook_end_time="00:08",
            transcript_evidence="Hook evidence",
            reasoning="Strong hook",
            hook_score=20,
        ),
    )
    source_ref = SourceAssetRef(source_id="src-200")

    broll_opps = [
        {"timestamp": "01:10", "duration": 3.0, "search_term": "nature", "local_path": "/tmp/nature.mp4"},
        {"timestamp": "03:00", "duration": 2.0, "search_term": "outside bounds"},
    ]
    sfx_opps = [
        {
            "source_timestamp": "00:06",
            "duration": 1.0,
            "query": "whoosh",
            "intensity": "moderate",
            "gain_db": -2.0,
            "placement": "hook",
            "asset_path": "/tmp/whoosh.mp3",
        },
        {
            "source_timestamp": "05:00",
            "duration": 1.0,
            "query": "outside bounds",
        },
    ]

    comp = CompositionBuilder.build_from_segment(
        segment=seg,
        source_ref=source_ref,
        broll_opps=broll_opps,
        sfx_opps=sfx_opps,
    )

    # 2 segments: hook + main
    assert len(comp.segments) == 2
    assert comp.segments[0].source_start == 5.0
    assert comp.segments[0].source_end == 8.0
    assert comp.segments[1].source_start == 60.0
    assert comp.segments[1].source_end == 90.0

    # B-roll: only the one at 01:10 (70s) is within [5.0, 90.0]
    assert len(comp.broll_inserts) == 1
    assert comp.broll_inserts[0].search_term == "nature"
    assert comp.broll_inserts[0].at_time == 65.0  # 70.0 - 5.0

    # SFX: only the one at 00:06 (6s) is within [5.0, 90.0]
    assert len(comp.sfx) == 1
    assert comp.sfx[0].query == "whoosh"
    assert comp.sfx[0].at_time == 1.0  # 6.0 - 5.0
    assert comp.sfx[0].gain_db == -2.0


def test_composition_builder_dict_segment_input():
    seg_dict = {
        "start_time": "00:00",
        "end_time": "00:15",
        "text": "Segment from dict",
        "broll_opportunities": [
            {"timestamp": "00:05", "duration": 2.0, "search_term": "city"}
        ],
        "sfx_opportunities": [
            {"source_timestamp": "00:02", "duration": 0.5, "query": "bell", "gain_db": -1.0}
        ],
    }
    source_ref = SourceAssetRef(source_id="src-dict")
    comp = CompositionBuilder.build_from_segment(seg_dict, source_ref=source_ref)

    assert comp.source_asset_ref.source_id == "src-dict"
    assert len(comp.segments) == 1
    assert comp.segments[0].source_start == 0.0
    assert comp.segments[0].source_end == 15.0
    assert len(comp.broll_inserts) == 1
    assert comp.broll_inserts[0].search_term == "city"
    assert comp.broll_inserts[0].at_time == 5.0
    assert len(comp.sfx) == 1
    assert comp.sfx[0].query == "bell"
    assert comp.sfx[0].at_time == 2.0

