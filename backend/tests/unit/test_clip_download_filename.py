import pytest

from src.api.routes.tasks import build_clip_download_filename


def test_non_empty_hook_title_is_used_as_download_filename():
    assert build_clip_download_filename(
        {
            "hook_title": "Traditional building is too slow",
            "clip_order": 2,
        }
    ) == "Traditional building is too slow.mp4"


@pytest.mark.parametrize("hook_title", ["", None], ids=["empty", "none"])
def test_empty_or_none_hook_title_falls_back_to_clip_order(hook_title):
    assert build_clip_download_filename(
        {
            "hook_title": hook_title,
            "clip_order": 2,
        }
    ) == "Clip 2.mp4"


def test_illegal_path_characters_are_removed_and_filename_ends_with_mp4():
    filename = build_clip_download_filename(
        {
            "hook_title": "../A:B?",
            "clip_order": 2,
        }
    )

    assert all(
        forbidden not in filename
        for forbidden in ("/", "\\", "..", ":", "?")
    ) and filename.endswith(".mp4")


def test_export_suffix_is_inserted_before_mp4_extension():
    assert build_clip_download_filename(
        {
            "hook_title": "Title",
            "clip_order": 2,
        },
        suffix="_tiktok",
    ) == "Title_tiktok.mp4"
