from src.api.routes.tasks import (
    _build_public_task,
    _merge_task_source_metadata,
    _normalize_font_color,
    _normalize_font_family,
    _normalize_font_size,
)


def test_normalize_font_size_bounds_values():
    assert _normalize_font_size(None) is None
    assert _normalize_font_size("") is None
    assert _normalize_font_size("4") == 12
    assert _normalize_font_size("120") == 72


def test_normalize_font_color_accepts_hex_values():
    assert _normalize_font_color(None) is None
    assert _normalize_font_color("  ") is None
    assert _normalize_font_color("#abcdef") == "#ABCDEF"
    assert _normalize_font_color("blue") == "#FFFFFF"


def test_normalize_font_family_passes_through_empty_values_as_none():
    assert _normalize_font_family(None) is None
    assert _normalize_font_family("  ") is None
    assert _normalize_font_family("Inter") == "Inter"


def test_build_public_task_excludes_owner_and_file_system_fields():
    public_task = _build_public_task(
        {
            "id": "task-1",
            "user_id": "owner-1",
            "source_title": "Shared source",
            "source_type": "youtube",
            "source_url": "https://private.example/video",
            "status": "completed",
            "clips_count": 1,
            "created_at": "2026-07-22T00:00:00Z",
            "updated_at": "2026-07-22T00:01:00Z",
            "clips": [
                {
                    "id": "clip-1",
                    "filename": "clip.mp4",
                    "file_path": "/private/clip.mp4",
                    "clip_order": 1,
                    "text": "A shared transcript",
                }
            ],
        },
        "token-1",
    )

    assert public_task["source_title"] == "Shared source"
    assert "user_id" not in public_task
    assert "source_url" not in public_task
    assert "file_path" not in public_task["clips"][0]
    assert public_task["clips"][0]["video_url"] == (
        "/tasks/shared/token-1/clips/clip-1/file"
    )


def test_merge_task_source_metadata_backfills_hook_persist_when_bool():
    # hook_persist=True is a valid bool and must be merged into the payload.
    merged = _merge_task_source_metadata({}, hook_persist=True)

    assert merged["hook_persist"] is True


def test_merge_task_source_metadata_ignores_non_bool_hook_persist():
    # Contract: _merge_task_source_metadata only overwrites hook_persist when
    # the incoming value is a bool. A string "yes" must leave the existing
    # True untouched.
    merged = _merge_task_source_metadata(
        {"hook_persist": True},
        hook_persist="yes",
    )

    assert merged["hook_persist"] is True


def test_merge_task_source_metadata_false_hook_persist_overwrites_existing():
    # False is a valid bool too: it must overwrite the existing True.
    merged = _merge_task_source_metadata(
        {"hook_persist": True},
        hook_persist=False,
    )

    assert merged["hook_persist"] is False


def test_merge_task_source_metadata_sets_watermark_when_non_empty_str():
    # watermark is a str/None plumbing field; it must be merged into the payload
    # only when the incoming value is a non-empty string.
    merged = _merge_task_source_metadata({}, watermark="x")

    assert merged["watermark"] == "x"


def test_merge_task_source_metadata_ignores_empty_or_none_watermark():
    # Contract: _merge_task_source_metadata only overwrites watermark when the
    # incoming value is a non-empty str. Empty string and None must leave the
    # existing value untouched.
    assert (
        _merge_task_source_metadata({"watermark": "keep"}, watermark="")["watermark"]
        == "keep"
    )
    assert (
        _merge_task_source_metadata({"watermark": "keep"}, watermark=None)["watermark"]
        == "keep"
    )


def test_merge_task_source_metadata_sets_watermark_persist_when_bool():
    # watermark_persist=True is a valid bool and must be merged into the payload.
    merged = _merge_task_source_metadata({}, watermark_persist=True)

    assert merged["watermark_persist"] is True


def test_merge_task_source_metadata_ignores_non_bool_watermark_persist():
    # Contract: _merge_task_source_metadata only overwrites watermark_persist
    # when the incoming value is a bool. A string "yes" must leave the existing
    # True untouched.
    merged = _merge_task_source_metadata(
        {"watermark_persist": True},
        watermark_persist="yes",
    )

    assert merged["watermark_persist"] is True
