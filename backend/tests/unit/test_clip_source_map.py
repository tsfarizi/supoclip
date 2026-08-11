from pathlib import Path

import pytest

from src.clip_source_map import (
    clip_source_map_path,
    copy_clip_source_ranges,
    load_clip_source_ranges,
    normalize_source_ranges,
    save_clip_source_ranges,
    slice_source_ranges,
    source_range_bounds,
    split_source_ranges,
    total_source_duration,
    trim_source_ranges,
)
from src.services.task_service import TaskService


def test_trim_source_ranges_preserves_non_contiguous_mapping():
    trimmed = trim_source_ranges([(10.0, 11.0), (13.0, 15.0)], 1.0, 0.0)

    assert trimmed == [(13.0, 15.0)]


def test_split_source_ranges_preserves_non_contiguous_mapping():
    first, second = split_source_ranges([(10.0, 11.0), (13.0, 15.0)], 1.0)

    assert first == [(10.0, 11.0)]
    assert second == [(13.0, 15.0)]


def test_save_and_load_clip_source_ranges(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"")

    save_clip_source_ranges(clip_path, [(10.0, 11.0), (13.0, 15.0)])

    assert load_clip_source_ranges(clip_path) == [(10.0, 11.0), (13.0, 15.0)]


class _FakeClipRepo:
    def __init__(self, clip: dict):
        self.clip = dict(clip)

    async def get_clip_by_id(self, _db, _clip_id: str):
        return dict(self.clip)

    async def update_clip(
        self,
        _db,
        _clip_id: str,
        filename: str,
        file_path: str,
        start_time: str,
        end_time: str,
        duration: float,
        text: str,
    ):
        self.clip.update(
            {
                "filename": filename,
                "file_path": file_path,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "text": text,
            }
        )


@pytest.mark.asyncio
async def test_trim_clip_uses_persisted_source_ranges(monkeypatch, tmp_path):
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"input")
    save_clip_source_ranges(input_path, [(10.0, 11.0), (13.0, 15.0)])

    output_path = tmp_path / "trimmed.mp4"
    output_path.write_bytes(b"output")

    monkeypatch.setattr(
        "src.services.task_service.trim_clip_file",
        lambda *_args, **_kwargs: output_path,
    )

    repo = _FakeClipRepo(
        {
            "id": "clip-1",
            "task_id": "task-1",
            "file_path": str(input_path),
            "filename": "clip.mp4",
            "start_time": "00:10",
            "end_time": "00:20",
            "duration": 3.0,
            "text": "hello",
        }
    )

    service = TaskService(db=None)
    service.clip_repo = repo

    clip = await service.trim_clip("task-1", "clip-1", 1.0, 0.0)

    assert clip["start_time"] == "00:13"
    assert clip["end_time"] == "00:15"
    assert clip["duration"] == pytest.approx(2.0)
    assert load_clip_source_ranges(output_path) == [(13.0, 15.0)]


@pytest.mark.asyncio
async def test_regenerate_all_clips_reuses_persisted_source_ranges(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 11.0), (13.0, 15.0)])

    captured: dict[str, object] = {}

    class _FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id: str):
            return {
                "id": "task-1",
                "source_url": "upload://source.mp4",
                "source_type": "video_url",
            }

        async def update_task_clips(self, _db, _task_id: str, _clip_ids):
            return None

    class _FakeClipRepoWithTask:
        async def get_clips_by_task(self, _db, _task_id: str):
            return [
                {
                    "id": "clip-1",
                    "task_id": "task-1",
                    "file_path": str(clip_path),
                    "start_time": "00:10",
                    "end_time": "00:20",
                    "duration": 3.0,
                    "text": "hello",
                    "relevance_score": 0.8,
                    "reasoning": "test",
                    "virality_score": 1,
                    "hook_score": 1,
                    "engagement_score": 1,
                    "value_score": 1,
                    "shareability_score": 1,
                    "hook_type": "hook",
                }
            ]

        async def delete_clips_by_task(self, _db, _task_id: str):
            return None

    class _FakeVideoService:
        def resolve_local_video_path(self, _url: str) -> Path:
            return source_path

        async def create_video_clips(self, _video_path: Path, segments, *_args, **_kwargs):
            captured["segments"] = segments
            return []

    service = TaskService(db=None)
    async def fake_load_task_source_settings(_task_id: str):
        return {
            "output_format": "vertical",
            "add_subtitles": True,
            "cut_long_pauses": False,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    monkeypatch.setattr(service, "_load_task_source_settings", fake_load_task_source_settings)
    service.task_repo = _FakeTaskRepo()
    service.clip_repo = _FakeClipRepoWithTask()
    service.video_service = _FakeVideoService()

    await service.regenerate_all_clips_for_task(
        "task-1",
        "TikTokSans-Regular",
        24,
        "#FFFFFF",
        "default",
        cleanup_settings={},
    )

    segments = captured["segments"]
    assert segments[0]["keep_ranges"] == [(10.0, 11.0), (13.0, 15.0)]


@pytest.mark.asyncio
async def test_regenerate_all_clips_recomputes_cleanup_from_source_ranges(
    monkeypatch, tmp_path
):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 11.0), (13.0, 15.0)])

    captured: dict[str, object] = {}

    class _FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id: str):
            return {
                "id": "task-1",
                "source_url": "upload://source.mp4",
                "source_type": "video_url",
            }

        async def update_task_clips(self, _db, _task_id: str, _clip_ids):
            return None

    class _FakeClipRepoWithTask:
        async def get_clips_by_task(self, _db, _task_id: str):
            return [
                {
                    "id": "clip-1",
                    "task_id": "task-1",
                    "file_path": str(clip_path),
                    "start_time": "00:10",
                    "end_time": "00:20",
                    "duration": 3.0,
                    "text": "hello",
                    "relevance_score": 0.8,
                    "reasoning": "test",
                    "virality_score": 1,
                    "hook_score": 1,
                    "engagement_score": 1,
                    "value_score": 1,
                    "shareability_score": 1,
                    "hook_type": "hook",
                }
            ]

        async def delete_clips_by_task(self, _db, _task_id: str):
            return None

    class _FakeVideoService:
        def resolve_local_video_path(self, _url: str) -> Path:
            return source_path

        async def create_video_clips(self, _video_path: Path, segments, *_args, **_kwargs):
            captured["segments"] = segments
            return []

    service = TaskService(db=None)

    async def fake_load_task_source_settings(_task_id: str):
        return {
            "output_format": "vertical",
            "add_subtitles": True,
            "cut_long_pauses": False,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    monkeypatch.setattr(service, "_load_task_source_settings", fake_load_task_source_settings)
    service.task_repo = _FakeTaskRepo()
    service.clip_repo = _FakeClipRepoWithTask()
    service.video_service = _FakeVideoService()

    await service.regenerate_all_clips_for_task(
        "task-1",
        "TikTokSans-Regular",
        24,
        "#FFFFFF",
        "default",
        cleanup_settings={"cut_long_pauses": True},
    )

    segments = captured["segments"]
    assert "keep_ranges" not in segments[0]
    assert segments[0]["source_ranges"] == [(10.0, 11.0), (13.0, 15.0)]


# ---------------------------------------------------------------------------
# Pure helpers: normalize_source_ranges, source_range_bounds,
# total_source_duration, slice_source_ranges, trim_source_ranges,
# split_source_ranges, save/load/copy edges.
# ---------------------------------------------------------------------------


class TestNormalizeSourceRanges:
    def test_valid_ranges_are_kept_in_order(self):
        assert normalize_source_ranges([(0.0, 5.0), (10.0, 15.0)]) == [
            (0.0, 5.0),
            (10.0, 15.0),
        ]

    def test_numeric_strings_are_coerced_to_float(self):
        assert normalize_source_ranges([("10", "11")]) == [(10.0, 11.0)]

    def test_min_duration_boundary(self):
        # 0.05s exactly is filtered; anything above survives.
        assert normalize_source_ranges([(0.0, 0.05)]) == []
        assert normalize_source_ranges([(0.0, 0.0500001)]) == [(0.0, 0.0500001)]

    def test_non_numeric_items_are_skipped(self):
        assert normalize_source_ranges([(None, 5.0), ("x", 5.0), (1.0, "y")]) == []
        assert normalize_source_ranges([(0.0, 5.0), (None, None)]) == [(0.0, 5.0)]

    def test_negative_or_reversed_ranges_are_filtered(self):
        # Only ranges with end-start <= MIN_RANGE_SECONDS are dropped; negative
        # coordinates still form a valid positive duration.
        assert normalize_source_ranges([(5.0, 2.0)]) == []
        assert normalize_source_ranges([(-5.0, -2.0)]) == [(-5.0, -2.0)]

    def test_none_and_empty_inputs_return_empty_list(self):
        assert normalize_source_ranges(None) == []
        assert normalize_source_ranges([]) == []

    def test_short_ranges_between_valid_ones_are_dropped(self):
        assert normalize_source_ranges([(0.0, 5.0), (1.0, 1.03), (10.0, 15.0)]) == [
            (0.0, 5.0),
            (10.0, 15.0),
        ]


class TestSourceRangeBounds:
    def test_bounds_span_first_start_to_last_end(self):
        assert source_range_bounds([(0.0, 5.0), (10.0, 15.0)]) == (0.0, 15.0)

    def test_single_range_returns_itself(self):
        assert source_range_bounds([(3.0, 8.0)]) == (3.0, 8.0)

    def test_empty_or_invalid_returns_none(self):
        assert source_range_bounds([]) is None
        assert source_range_bounds(None) is None
        assert source_range_bounds([(0.0, 0.01)]) is None
        assert source_range_bounds([("bad", 5.0)]) is None


class TestTotalSourceDuration:
    def test_sums_valid_ranges(self):
        assert total_source_duration([(0.0, 5.0), (10.0, 15.0)]) == 10.0

    def test_empty_inputs_sum_to_zero(self):
        assert total_source_duration([]) == 0.0
        assert total_source_duration(None) == 0.0

    def test_invalid_ranges_are_excluded_from_sum(self):
        assert total_source_duration([(0.0, 5.0), ("bad", 9.0)]) == 5.0


class TestSliceSourceRanges:
    def test_window_inside_first_segment(self):
        assert slice_source_ranges([(0.0, 5.0), (10.0, 15.0)], 2.0, 4.0) == [
            (2.0, 4.0)
        ]

    def test_window_spanning_segments_preserves_source_gaps(self):
        assert slice_source_ranges([(0.0, 5.0), (10.0, 15.0)], 2.0, 8.0) == [
            (2.0, 5.0),
            (10.0, 13.0),
        ]

    def test_window_inside_second_segment(self):
        assert slice_source_ranges([(0.0, 5.0), (10.0, 15.0)], 6.0, 9.0) == [
            (11.0, 14.0)
        ]

    def test_window_beyond_total_returns_empty(self):
        assert slice_source_ranges([(0.0, 5.0), (10.0, 15.0)], 12.0, 14.0) == []

    def test_window_beyond_end_is_clamped(self):
        assert slice_source_ranges([(0.0, 5.0)], 2.0, 99.0) == [(2.0, 5.0)]

    def test_negative_start_clamps_to_zero(self):
        assert slice_source_ranges([(0.0, 5.0)], -3.0, 2.0) == [(0.0, 2.0)]

    def test_fragment_shorter_than_min_is_dropped(self):
        assert slice_source_ranges([(0.0, 5.0)], 4.9, 4.95) == []
        # Window past the source end clamps to the source boundary.
        assert slice_source_ranges([(0.0, 5.0)], 4.9, 5.01) == [(4.9, 5.0)]


class TestTrimSourceRanges:
    def test_trim_offset_into_non_contiguous_mapping(self):
        assert trim_source_ranges([(10.0, 11.0), (13.0, 15.0)], 1.0, 0.0) == [
            (13.0, 15.0)
        ]

    def test_trim_from_both_ends(self):
        assert trim_source_ranges([(0.0, 5.0), (10.0, 15.0)], 3.0, 2.0) == [
            (3.0, 5.0),
            (10.0, 13.0),
        ]

    def test_trim_more_than_total_duration_returns_empty(self):
        assert trim_source_ranges([(0.0, 5.0)], 10.0, 0.0) == []
        assert trim_source_ranges([(0.0, 5.0)], 0.0, 10.0) == []

    def test_negative_offsets_are_clamped(self):
        assert trim_source_ranges([(0.0, 5.0)], -1.0, -2.0) == [(0.0, 5.0)]
        assert trim_source_ranges([(0.0, 5.0)], -1.0, 2.0) == [(0.0, 3.0)]


class TestSplitSourceRanges:
    def test_split_at_zero_yields_empty_first(self):
        first, second = split_source_ranges([(0.0, 10.0)], 0.0)
        assert first == []
        assert second == [(0.0, 10.0)]

    def test_split_at_total_yields_empty_second(self):
        first, second = split_source_ranges([(0.0, 10.0)], 10.0)
        assert first == [(0.0, 10.0)]
        assert second == []

    def test_split_mid_range_splits_one_range(self):
        first, second = split_source_ranges([(0.0, 10.0)], 3.0)
        assert first == [(0.0, 3.0)]
        assert second == [(3.0, 10.0)]

    def test_split_between_segments(self):
        first, second = split_source_ranges(
            [(0.0, 5.0), (10.0, 15.0)], 5.0
        )
        assert first == [(0.0, 5.0)]
        assert second == [(10.0, 15.0)]

    def test_negative_split_clamps_to_zero(self):
        first, second = split_source_ranges([(0.0, 10.0)], -5.0)
        assert first == []
        assert second == [(0.0, 10.0)]

    def test_overshoot_split_clamps_to_total(self):
        first, second = split_source_ranges([(0.0, 10.0)], 99.0)
        assert first == [(0.0, 10.0)]
        assert second == []


class TestSaveLoadCopyEdges:
    def test_clip_source_map_path_suffix(self):
        assert clip_source_map_path(Path("clip.mp4")) == Path("clip.source_map.json")

    def test_save_rounds_to_six_decimals(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        save_clip_source_ranges(clip_path, [(1.0 / 3.0, 2.0 / 3.0)])
        payload = clip_path.with_suffix(".source_map.json").read_text()
        assert '"start": 0.333333' in payload
        assert '"end": 0.666667' in payload

    def test_save_with_invalid_ranges_removes_existing_map(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        save_clip_source_ranges(clip_path, [(0.0, 5.0)])
        assert clip_source_map_path(clip_path).exists()
        save_clip_source_ranges(clip_path, [])
        assert not clip_source_map_path(clip_path).exists()
        assert load_clip_source_ranges(clip_path) is None

    def test_load_missing_file_returns_none(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        assert load_clip_source_ranges(clip_path) is None

    def test_load_malformed_json_returns_none(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        clip_source_map_path(clip_path).write_text("{not json", encoding="utf-8")
        assert load_clip_source_ranges(clip_path) is None

    def test_load_payload_without_source_ranges_returns_none(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        clip_source_map_path(clip_path).write_text(
            '{"version": 1, "other": 1}', encoding="utf-8"
        )
        assert load_clip_source_ranges(clip_path) is None

    def test_load_non_dict_items_are_skipped(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        clip_source_map_path(clip_path).write_text(
            '{"source_ranges": [42, {"start": 0, "end": 5}, "x"]}', encoding="utf-8"
        )
        assert load_clip_source_ranges(clip_path) == [(0.0, 5.0)]

    def test_load_normalizes_numeric_strings(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        clip_source_map_path(clip_path).write_text(
            '{"source_ranges": [{"start": "10", "end": "11"}]}', encoding="utf-8"
        )
        assert load_clip_source_ranges(clip_path) == [(10.0, 11.0)]

    def test_load_all_invalid_ranges_returns_none(self, tmp_path):
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"")
        clip_source_map_path(clip_path).write_text(
            '{"source_ranges": [{"start": 0, "end": 0.01}]}', encoding="utf-8"
        )
        assert load_clip_source_ranges(clip_path) is None

    def test_copy_preserves_ranges(self, tmp_path):
        source = tmp_path / "source.mp4"
        target = tmp_path / "target.mp4"
        source.write_bytes(b"")
        target.write_bytes(b"")
        save_clip_source_ranges(source, [(10.0, 11.0), (13.0, 15.0)])
        copy_clip_source_ranges(source, target)
        assert load_clip_source_ranges(target) == [(10.0, 11.0), (13.0, 15.0)]

    def test_copy_without_source_removes_target_map(self, tmp_path):
        source = tmp_path / "source.mp4"
        target = tmp_path / "target.mp4"
        source.write_bytes(b"")
        target.write_bytes(b"")
        save_clip_source_ranges(target, [(0.0, 5.0)])
        copy_clip_source_ranges(source, target)
        assert not clip_source_map_path(target).exists()
        assert load_clip_source_ranges(target) is None
