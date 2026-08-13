"""Falsification tests for the YuNet model asset resolver (src/model_assets.py).

Contracts (U9/D3): an explicit local path is used when valid; a cached model
is reused without re-downloading; a fresh download is attempted from Hugging
Face when nothing is cached; download/probe failures degrade to None so the
detector chain falls back to MediaPipe/Haar; the per-process memo prevents
repeated network attempts. All network/probe work is monkeypatched so the
tests are hermetic.
"""

from pathlib import Path

import pytest

from src import model_assets
from src.config import Config, set_config_override


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    model_assets.reset_resolve_cache_for_tests()
    monkeypatch.setattr(model_assets, "MIN_MODEL_SIZE_BYTES", 1)
    yield
    model_assets.reset_resolve_cache_for_tests()


def _config(tmp_path, **overrides):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.yunet_model_path = None
    config.yunet_model_repo = None
    config.yunet_model_file = None
    config.yunet_auto_download = True
    for key, value in overrides.items():
        setattr(config, key, value)
    set_config_override(config)
    return config


def _fake_onnx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 4096)


def test_explicit_path_is_used_when_valid(tmp_path, monkeypatch):
    model = tmp_path / "m.onnx"
    _fake_onnx(model)
    _config(tmp_path, yunet_model_path=str(model))
    monkeypatch.setattr(model_assets, "_probe_model", lambda p: True)

    result = model_assets.resolve_yunet_model()
    assert result == model


def test_explicit_invalid_path_falls_through_to_download(tmp_path, monkeypatch):
    _config(tmp_path, yunet_model_path=str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(model_assets, "_probe_model", lambda p: True)
    downloaded = tmp_path / "models" / model_assets.YUNET_CANDIDATES[0][1]
    monkeypatch.setattr(
        model_assets,
        "_download",
        lambda _url, dest: (_fake_onnx(dest) or True),
    )

    result = model_assets.resolve_yunet_model()
    assert result == downloaded


def test_cached_model_is_reused_without_download(tmp_path, monkeypatch):
    _config(tmp_path)
    cached = tmp_path / "models" / model_assets.YUNET_CANDIDATES[0][1]
    _fake_onnx(cached)
    monkeypatch.setattr(model_assets, "_probe_model", lambda p: True)
    download_calls = {"n": 0}
    monkeypatch.setattr(
        model_assets, "_download", lambda _url, dest: download_calls.__setitem__("n", download_calls["n"] + 1) or True
    )

    result = model_assets.resolve_yunet_model()
    assert result == cached
    assert download_calls["n"] == 0


def test_download_failure_returns_none(tmp_path, monkeypatch):
    _config(tmp_path)
    monkeypatch.setattr(model_assets, "_probe_model", lambda p: False)
    monkeypatch.setattr(model_assets, "_download", lambda _url, dest: False)

    result = model_assets.resolve_yunet_model()
    assert result is None


def test_auto_download_disabled_returns_none(tmp_path):
    _config(tmp_path, yunet_auto_download=False)

    result = model_assets.resolve_yunet_model()
    assert result is None


def test_negative_result_is_memoized(tmp_path, monkeypatch):
    _config(tmp_path)
    monkeypatch.setattr(model_assets, "_probe_model", lambda p: False)
    monkeypatch.setattr(model_assets, "_download", lambda _url, dest: False)

    assert model_assets.resolve_yunet_model() is None
    calls = {"n": 0}
    monkeypatch.setattr(model_assets, "_download", lambda _url, dest: calls.__setitem__("n", calls["n"] + 1) or False)

    # Memoized negative: no second download attempt.
    assert model_assets.resolve_yunet_model() is None
    assert calls["n"] == 0


def test_candidate_order_official_repo_first(tmp_path):
    _config(tmp_path)
    sources = model_assets._candidate_sources()
    assert sources == model_assets.YUNET_CANDIDATES
    assert sources[0][0] == "opencv/face_detection_yunet"


def test_repo_override_changes_source(tmp_path):
    _config(tmp_path, yunet_model_repo="custom/repo", yunet_model_file="m.onnx")
    sources = model_assets._candidate_sources()
    assert sources == [("custom/repo", "m.onnx")]
