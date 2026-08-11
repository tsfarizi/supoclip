"""
Falsification tests for the font registry pure helpers.

Covered contracts (src/font_registry.py):
  1. sanitize_user_id_for_path / get_user_fonts_dir: user id -> safe path
     segment, falling back to "user" for empty/symbol-only ids.
  2. sanitize_font_stem: file name -> safe stem, ValueError when nothing
     survives sanitization.
  3. build_user_font_stem: prefixed, lower-cased "usr-<user>-<stem>" handle.
  4. find_user_font_path / find_font_path / is_font_accessible: exact + stem
     resolution inside the sandbox, supported extensions only, per-user
     isolation.
  5. get_available_fonts / _collect_fonts_from_dir: system + user listing,
     sorted by display name, unsupported extensions ignored.

Filesystem access is hermetically redirected to tmp_path via monkeypatch
(FONTS_DIR + USER_FONTS_DIR), so no repo font files are read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import font_registry


@pytest.fixture()
def isolated_font_dirs(monkeypatch, tmp_path):
    system_dir = tmp_path / "system-fonts"
    user_dir = tmp_path / "user-fonts"
    system_dir.mkdir()
    user_dir.mkdir()
    monkeypatch.setattr(font_registry, "FONTS_DIR", system_dir)
    monkeypatch.setattr(font_registry, "USER_FONTS_DIR", user_dir)
    return system_dir, user_dir


class TestSanitizeUserIdForPath:
    def test_allows_alnum_underscore_hyphen(self):
        assert font_registry.sanitize_user_id_for_path("user-1_aB") == "user-1_aB"

    def test_unsafe_chars_become_hyphen(self):
        assert font_registry.sanitize_user_id_for_path("a b@c") == "a-b-c"

    def test_leading_trailing_hyphens_are_stripped(self):
        assert font_registry.sanitize_user_id_for_path("-user-1-") == "user-1"

    def test_empty_or_symbol_only_falls_back_to_user(self):
        assert font_registry.sanitize_user_id_for_path("") == "user"
        assert font_registry.sanitize_user_id_for_path("!!!") == "user"


class TestGetUserFontsDir:
    def test_appends_sanitized_id(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        assert font_registry.get_user_fonts_dir("u@ser 1") == user_dir / "u-ser-1"


class TestSanitizeFontStem:
    def test_basic_stem_extraction(self):
        assert font_registry.sanitize_font_stem("Inter.ttf") == "Inter"
        assert font_registry.sanitize_font_stem("My Font.ttf") == "My-Font"

    def test_unsafe_chars_become_hyphen(self):
        assert font_registry.sanitize_font_stem("Bold(Copy).ttf") == "Bold-Copy"

    def test_strip_leading_trailing_hyphens(self):
        assert font_registry.sanitize_font_stem("-Inter-") == "Inter"

    def test_case_is_preserved(self):
        assert font_registry.sanitize_font_stem("MyFont.ttf") == "MyFont"

    def test_path_like_input_uses_stem_only(self):
        assert font_registry.sanitize_font_stem("dir/sub/MyFont.ttf") == "MyFont"

    def test_nothing_left_raises_value_error(self):
        with pytest.raises(ValueError):
            font_registry.sanitize_font_stem("!!!")
        with pytest.raises(ValueError):
            font_registry.sanitize_font_stem("....")


class TestBuildUserFontStem:
    def test_basic_prefix_and_lowercase(self):
        assert (
            font_registry.build_user_font_stem("user-1", "My Font.ttf")
            == "usr-user-1-my-font"
        )

    def test_user_id_is_sanitized(self):
        assert (
            font_registry.build_user_font_stem("User@1", "Brand.ttf")
            == "usr-user-1-brand"
        )

    def test_invalid_original_stem_raises(self):
        with pytest.raises(ValueError):
            font_registry.build_user_font_stem("user-1", "###")


class TestFindUserFontPath:
    def test_exact_file_is_resolved(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        owned = user_dir / "user-1" / "usr-user-1-brand.ttf"
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"font")
        assert font_registry.find_user_font_path("usr-user-1-brand.ttf", "user-1") == owned

    def test_stem_tries_supported_extensions_in_order(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "Brand.ttf").write_bytes(b"font")
        assert font_registry.find_user_font_path("Brand", "user-1") == owned / "Brand.ttf"
        # Only the .otf exists -> resolved via the second extension.
        (owned / "Other.otf").write_bytes(b"font")
        assert font_registry.find_user_font_path("Other", "user-1") == owned / "Other.otf"

    def test_unsupported_extension_is_not_resolved(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "Brand.txt").write_bytes(b"font")
        assert font_registry.find_user_font_path("Brand", "user-1") is None
        assert font_registry.find_user_font_path("Brand.txt", "user-1") is None

    def test_empty_name_is_rejected(self, isolated_font_dirs):
        assert font_registry.find_user_font_path("", "user-1") is None
        assert font_registry.find_user_font_path("   ", "user-1") is None

    def test_path_traversal_is_rejected(self, isolated_font_dirs):
        system_dir, user_dir = isolated_font_dirs
        # Trap file inside the parent sandbox root that traversal would reach.
        (user_dir / "evil.ttf").write_bytes(b"font")
        assert font_registry.find_user_font_path("../evil", "user-1") is None
        assert font_registry.find_user_font_path("..%2fevil", "user-1") is None
        assert system_dir.is_dir()

    def test_missing_font_returns_none(self, isolated_font_dirs):
        assert font_registry.find_user_font_path("Nope", "user-1") is None

    def test_missing_user_dir_returns_none(self, isolated_font_dirs):
        assert font_registry.find_user_font_path("Brand", "nobody") is None


class TestFindFontPath:
    def test_user_font_wins_over_system(self, isolated_font_dirs):
        system_dir, user_dir = isolated_font_dirs
        (system_dir / "Brand.ttf").write_bytes(b"system")
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "Brand.ttf").write_bytes(b"user")
        resolved = font_registry.find_font_path("Brand", user_id="user-1")
        assert resolved == owned / "Brand.ttf"

    def test_wrong_user_cannot_see_another_users_font(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "usr-user-1-brand.ttf").write_bytes(b"font")
        assert font_registry.find_font_path("usr-user-1-brand", user_id="user-2") is None

    def test_system_font_is_visible_to_any_user(self, isolated_font_dirs):
        system_dir, _ = isolated_font_dirs
        (system_dir / "Inter.ttf").write_bytes(b"font")
        assert (
            font_registry.find_font_path("Inter", user_id="user-1")
            == system_dir / "Inter.ttf"
        )
        assert (
            font_registry.find_font_path("Inter", user_id="user-2")
            == system_dir / "Inter.ttf"
        )

    def test_normalized_alphanumeric_match(self, isolated_font_dirs):
        system_dir, _ = isolated_font_dirs
        (system_dir / "TikTokSans-Regular.ttf").write_bytes(b"font")
        assert (
            font_registry.find_font_path("tiktoksans regular", user_id="user-1")
            == system_dir / "TikTokSans-Regular.ttf"
        )

    def test_empty_name_returns_none(self, isolated_font_dirs):
        assert font_registry.find_font_path("", user_id="user-1") is None


class TestIsFontAccessible:
    def test_system_font_is_accessible_to_any_user(self, isolated_font_dirs):
        system_dir, _ = isolated_font_dirs
        (system_dir / "Inter.ttf").write_bytes(b"font")
        assert font_registry.is_font_accessible("Inter", "user-1") is True
        assert font_registry.is_font_accessible("Inter", "user-2") is True

    def test_own_user_font_is_accessible(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "usr-user-1-brand.ttf").write_bytes(b"font")
        assert font_registry.is_font_accessible("usr-user-1-brand", "user-1") is True

    def test_other_users_font_is_not_accessible(self, isolated_font_dirs):
        _, user_dir = isolated_font_dirs
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "usr-user-1-brand.ttf").write_bytes(b"font")
        assert font_registry.is_font_accessible("usr-user-1-brand", "user-2") is False

    def test_missing_font_is_not_accessible(self, isolated_font_dirs):
        assert font_registry.is_font_accessible("Nope", "user-1") is False

    def test_empty_name_is_not_accessible(self, isolated_font_dirs):
        assert font_registry.is_font_accessible("", "user-1") is False

    def test_unsupported_extension_is_not_accessible(self, isolated_font_dirs):
        system_dir, _ = isolated_font_dirs
        (system_dir / "Note.txt").write_bytes(b"font")
        assert font_registry.is_font_accessible("Note", "user-1") is False

    def test_path_traversal_cannot_escape_font_sandbox(self, isolated_font_dirs):
        system_dir, user_dir = isolated_font_dirs
        # Trap file OUTSIDE the fonts sandbox that "../evil" resolves to.
        (user_dir.parent / "evil.ttf").write_bytes(b"font")
        assert system_dir.is_dir()
        assert font_registry.is_font_accessible("../evil", "user-1") is False


class TestGetAvailableFonts:
    def test_empty_dirs_return_empty_list(self, isolated_font_dirs):
        assert font_registry.get_available_fonts() == []
        assert font_registry.get_available_fonts("user-1") == []

    def test_system_fonts_are_collected_with_metadata(self, isolated_font_dirs):
        system_dir, _ = isolated_font_dirs
        (system_dir / "Inter.ttf").write_bytes(b"font")
        (system_dir / "Bold-Custom.otf").write_bytes(b"font")
        (system_dir / "readme.txt").write_bytes(b"ignore")
        fonts = font_registry.get_available_fonts()
        assert len(fonts) == 2
        by_name = {f["name"]: f for f in fonts}
        assert by_name["Inter"] == {
            "name": "Inter",
            "display_name": "Inter",
            "filename": "Inter.ttf",
            "format": "ttf",
            "file_path": str(system_dir / "Inter.ttf"),
            "scope": "system",
        }
        assert by_name["Bold-Custom"]["display_name"] == "Bold Custom"
        assert by_name["Bold-Custom"]["format"] == "otf"

    def test_user_fonts_included_only_when_user_id_given(self, isolated_font_dirs):
        system_dir, user_dir = isolated_font_dirs
        (system_dir / "Inter.ttf").write_bytes(b"font")
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "usr-user-1-brand.ttf").write_bytes(b"font")

        only_system = font_registry.get_available_fonts()
        assert [f["name"] for f in only_system] == ["Inter"]

        with_user = font_registry.get_available_fonts("user-1")
        scopes = {f["name"]: f["scope"] for f in with_user}
        assert scopes["Inter"] == "system"
        assert scopes["usr-user-1-brand"] == "user"
        assert len(with_user) == 2

    def test_results_are_sorted_by_display_name(self, isolated_font_dirs):
        system_dir, user_dir = isolated_font_dirs
        (system_dir / "Zulu.ttf").write_bytes(b"font")
        (system_dir / "Alpha.ttf").write_bytes(b"font")
        owned = user_dir / "user-1"
        owned.mkdir()
        (owned / "mid-font.ttf").write_bytes(b"font")
        names = [f["display_name"] for f in font_registry.get_available_fonts("user-1")]
        assert names == sorted(names)
        assert names[0] == "Alpha"
        assert names[-1] == "Zulu"

    def test_nonexistent_font_dir_is_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setattr(font_registry, "FONTS_DIR", tmp_path / "missing")
        monkeypatch.setattr(font_registry, "USER_FONTS_DIR", tmp_path / "missing" / "users")
        assert font_registry.get_available_fonts() == []
        assert font_registry.get_available_fonts("user-1") == []
