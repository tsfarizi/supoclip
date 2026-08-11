"""
Falsification tests for caption template resolution (src/caption_templates.py).

Covered contracts:
  1. get_template: unknown names fall back to "default"; every result merges
     TEMPLATE_DEFAULTS with the template so renderers can .get() freely; a
     fresh dict is returned per call (mutation is safe).
  2. get_template_names: ordered list of the registered template keys.
  3. get_template_info: one info record per registered template with the
     documented API fields.
"""

from __future__ import annotations

import pytest

from src.caption_templates import (
    CAPTION_TEMPLATES,
    TEMPLATE_DEFAULTS,
    get_all_templates,
    get_template,
    get_template_info,
    get_template_names,
)


class TestGetTemplate:
    def test_unknown_name_returns_default(self):
        result = get_template("does-not-exist")
        assert result["name"] == CAPTION_TEMPLATES["default"]["name"]
        assert result["font_family"] == "THEBOLDFONT"

    def test_known_template_is_returned(self):
        assert get_template("hormozi")["name"] == "Hormozi"
        assert get_template("minimal")["name"] == "Minimal"

    def test_every_result_contains_all_default_keys(self):
        for name in get_template_names():
            merged = get_template(name)
            for key, default_value in TEMPLATE_DEFAULTS.items():
                assert key in merged, f"{name} missing {key}"

    def test_template_specific_values_override_defaults(self):
        assert get_template("hormozi")["word_box"] is True
        assert get_template("minimal")["emoji"] is False
        assert get_template("mrbeast")["font_color"] == "#FFFF00"
        assert get_template("neon")["glow"] is True

    def test_returned_dict_is_a_copy(self):
        template = get_template("default")
        template["font_size"] = 999
        assert get_template("default")["font_size"] == CAPTION_TEMPLATES["default"][
            "font_size"
        ]
        assert CAPTION_TEMPLATES["default"]["font_size"] != 999

    def test_all_registered_names_resolve(self):
        for name in CAPTION_TEMPLATES:
            merged = get_template(name)
            assert merged["name"] == CAPTION_TEMPLATES[name]["name"]

    def test_default_template_is_always_present(self):
        assert "default" in CAPTION_TEMPLATES
        assert get_template("default")["animation"] == "karaoke"


class TestGetTemplateNames:
    def test_returns_registered_template_keys_in_order(self):
        assert get_template_names() == [
            "default",
            "hormozi",
            "mrbeast",
            "minimal",
            "tiktok",
            "neon",
            "podcast",
        ]

    def test_matches_module_registry(self):
        assert get_template_names() == list(CAPTION_TEMPLATES.keys())


class TestGetAllTemplates:
    def test_returns_registry_as_is(self):
        assert get_all_templates() is CAPTION_TEMPLATES


class TestGetTemplateInfo:
    def test_one_record_per_template_with_documented_fields(self):
        info = get_template_info()
        assert len(info) == len(CAPTION_TEMPLATES)
        for record in info:
            assert set(record.keys()) == {
                "id",
                "name",
                "description",
                "animation",
                "font_family",
                "font_size",
                "font_color",
                "highlight_color",
            }
        assert {r["id"] for r in info} == set(CAPTION_TEMPLATES.keys())
