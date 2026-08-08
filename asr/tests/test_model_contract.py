"""Offline contract tests for ModelRuntime.

GPU/torch are never required: torch and qwen_asr are not installed in the
test environment, and these tests only exercise paths that must work without
them. The lazy-import boundary is itself part of the contract under test.

Contract clauses under falsification:
- Fake mode (ASR_FAKE_MODEL=1): load() sets ready; transcribe() returns a
  deterministic, contract-shaped payload with non-empty words and
  timestamps=True; never touches torch/qwen_asr.
- Real mode: construction and status() are lazy and offline-safe; load()
  without torch fails fast and leaves the runtime not ready.
- _to_ms timestamp conversion for the documented cases.
"""

from __future__ import annotations

import sys

import pytest

from src.config import AsrConfig
from src.model import ModelRuntime, _to_ms
from src.schemas import TranscribeResponse


def _fake_config() -> AsrConfig:
    """Fresh AsrConfig with ASR_FAKE_MODEL=1 via the documented env switch."""
    # Env is monkeypatched by each test; constructing AsrConfig directly (not
    # via the lru_cached get_config) keeps the cache free of test env.
    return AsrConfig()


# ------------------------------------------------------------- fake mode

def test_fake_mode_load_sets_ready(monkeypatch) -> None:
    monkeypatch.setenv("ASR_FAKE_MODEL", "1")
    rt = ModelRuntime(_fake_config())
    assert rt.is_ready is False
    rt.load()
    assert rt.is_ready is True


def test_fake_mode_transcribe_returns_contract_shape(monkeypatch) -> None:
    monkeypatch.setenv("ASR_FAKE_MODEL", "1")
    cfg = _fake_config()
    rt = ModelRuntime(cfg)
    rt.load()
    result = rt.transcribe("anything.wav")
    assert set(result.keys()) == {
        "language", "text", "words", "timestamps",
        "duration_ms", "elapsed_ms", "model", "aligner",
    }
    assert result["language"] == "en"
    assert result["text"] == "Hello world from fake ASR."
    assert isinstance(result["words"], list) and len(result["words"]) == 5
    for word in result["words"]:
        assert set(word.keys()) == {"text", "start_ms", "end_ms"}
        assert isinstance(word["start_ms"], int)
        assert isinstance(word["end_ms"], int)
    assert result["timestamps"] is True
    assert result["duration_ms"] == 1500
    assert result["elapsed_ms"] == 0
    assert result["model"] == cfg.asr_model
    assert result["aligner"] == cfg.asr_aligner
    # The fake payload must satisfy the documented response schema.
    TranscribeResponse(**result)


def test_fake_mode_transcribe_is_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("ASR_FAKE_MODEL", "1")
    rt = ModelRuntime(_fake_config())
    rt.load()
    first = rt.transcribe("a.wav")
    second = rt.transcribe("b.wav")
    assert first == second
    assert first["words"][0] == {"text": "Hello", "start_ms": 0, "end_ms": 300}


def test_fake_mode_never_touches_torch_or_qwen_asr(monkeypatch) -> None:
    # Blocking torch/qwen_asr imports turns any accidental import into an
    # ImportError, proving fake mode stays inside the lazy-import boundary.
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "qwen_asr", None)
    monkeypatch.setenv("ASR_FAKE_MODEL", "1")
    rt = ModelRuntime(_fake_config())
    rt.load()
    assert rt.is_ready is True
    result = rt.transcribe("anything.wav")
    assert result["text"] == "Hello world from fake ASR."


# ------------------------------------------------------------- real mode

def test_real_mode_construction_is_lazy_and_offline_safe(monkeypatch) -> None:
    monkeypatch.setenv("ASR_FAKE_MODEL", "0")
    rt = ModelRuntime(AsrConfig())
    assert rt.is_ready is False
    assert rt.status() == {"gpu": None, "vram_free_mb": None}
    # Merely constructing/running status() must not pull torch or qwen_asr in.
    assert sys.modules.get("torch") is None
    assert sys.modules.get("qwen_asr") is None


def test_real_mode_load_without_torch_fails_fast(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setenv("ASR_FAKE_MODEL", "0")
    rt = ModelRuntime(AsrConfig())
    with pytest.raises(ImportError):
        rt.load()
    assert rt.is_ready is False
    assert rt.status() == {"gpu": None, "vram_free_mb": None}


# ------------------------------------------------------------- _to_ms

def test_to_ms_documented_conversions() -> None:
    # Contract (U6): _to_ms ALWAYS converts seconds to milliseconds via
    # int(round(float(value) * 1000)) — there is no "already-ms" heuristic.
    assert _to_ms(0.3) == 300
    assert _to_ms(59.5) == 59500
    assert _to_ms(1500) == 1500000
    assert _to_ms(0) == 0
    assert _to_ms(0.999) == 999
    assert _to_ms(1000) == 1000000
