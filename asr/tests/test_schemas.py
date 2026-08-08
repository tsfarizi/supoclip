"""Contract shape tests for the ASR service schemas and error taxonomy.

Every test pins one clause of the documented HTTP contract:
- Word / TranscribeResponse / HealthResponse / ErrorResponse accept valid
  payloads and reject missing fields or wrong types.
- Documented defaults (gpu=None, vram_free_mb=None, queue_depth=0,
  trace_id=None) hold.
- The error taxonomy constants map to the documented status/retryable codes.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.errors import (
    AUDIO_INVALID,
    AUDIO_TOO_LARGE,
    INFERENCE_FAILED,
    MODEL_NOT_READY,
    TIMEOUT,
    VALIDATION,
    AsrError,
    build_error_response,
)
from src.schemas import ErrorResponse, HealthResponse, TranscribeResponse, Word


# ---------------------------------------------------------------- Word

def test_word_accepts_valid_payload() -> None:
    word = Word(text="Hello", start_ms=0, end_ms=300)
    assert word.text == "Hello"
    assert word.start_ms == 0
    assert word.end_ms == 300


def test_word_rejects_missing_text() -> None:
    with pytest.raises(ValidationError):
        Word(start_ms=0, end_ms=300)  # type: ignore[call-arg]


def test_word_rejects_non_string_text() -> None:
    with pytest.raises(ValidationError):
        Word(text=123, start_ms=0, end_ms=300)


def test_word_rejects_missing_start_ms() -> None:
    with pytest.raises(ValidationError):
        Word(text="Hello", end_ms=300)  # type: ignore[call-arg]


def test_word_rejects_wrong_type_timestamp() -> None:
    with pytest.raises(ValidationError):
        Word(text="Hello", start_ms="not-an-int", end_ms=300)


# ----------------------------------------------------- TranscribeResponse

def _valid_transcribe_payload() -> dict:
    return {
        "language": "en",
        "text": "Hello world",
        "words": [
            {"text": "Hello", "start_ms": 0, "end_ms": 300},
            {"text": "world", "start_ms": 300, "end_ms": 600},
        ],
        "timestamps": True,
        "duration_ms": 600,
        "elapsed_ms": 42,
        "model": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
    }


def test_transcribe_response_accepts_valid_payload() -> None:
    payload = _valid_transcribe_payload()
    resp = TranscribeResponse(**payload)
    assert resp.language == "en"
    assert resp.text == "Hello world"
    assert len(resp.words) == 2
    assert isinstance(resp.words[0], Word)
    assert resp.timestamps is True
    assert resp.duration_ms == 600
    assert resp.elapsed_ms == 42
    assert resp.model.startswith("Qwen/")
    assert resp.aligner.startswith("Qwen/")


def test_transcribe_response_accepts_empty_words() -> None:
    payload = _valid_transcribe_payload()
    payload["words"] = []
    payload["timestamps"] = False
    payload["duration_ms"] = 0
    resp = TranscribeResponse(**payload)
    assert resp.words == []
    assert resp.timestamps is False
    assert resp.duration_ms == 0


def test_transcribe_response_rejects_missing_language() -> None:
    payload = _valid_transcribe_payload()
    del payload["language"]
    with pytest.raises(ValidationError):
        TranscribeResponse(**payload)


def test_transcribe_response_rejects_missing_text() -> None:
    payload = _valid_transcribe_payload()
    del payload["text"]
    with pytest.raises(ValidationError):
        TranscribeResponse(**payload)


def test_transcribe_response_rejects_missing_words() -> None:
    payload = _valid_transcribe_payload()
    del payload["words"]
    with pytest.raises(ValidationError):
        TranscribeResponse(**payload)


def test_transcribe_response_rejects_wrong_timestamps_type() -> None:
    payload = _valid_transcribe_payload()
    payload["timestamps"] = "not-a-bool"  # "yes"/"no" would coerce in lax mode
    with pytest.raises(ValidationError):
        TranscribeResponse(**payload)


def test_transcribe_response_rejects_malformed_word_entry() -> None:
    payload = _valid_transcribe_payload()
    payload["words"] = [{"text": "orphan"}]  # missing start_ms/end_ms
    with pytest.raises(ValidationError):
        TranscribeResponse(**payload)


# ------------------------------------------------------- HealthResponse

def test_health_response_defaults_as_documented() -> None:
    resp = HealthResponse(status="ok", model_ready=True)
    assert resp.status == "ok"
    assert resp.model_ready is True
    assert resp.gpu is None
    assert resp.vram_free_mb is None
    assert resp.queue_depth == 0


def test_health_response_accepts_full_payload() -> None:
    resp = HealthResponse(
        status="ok",
        model_ready=True,
        gpu="NVIDIA RTX 4090",
        vram_free_mb=16384,
        queue_depth=2,
    )
    assert resp.gpu == "NVIDIA RTX 4090"
    assert resp.vram_free_mb == 16384
    assert resp.queue_depth == 2


def test_health_response_rejects_missing_status() -> None:
    with pytest.raises(ValidationError):
        HealthResponse(model_ready=True)  # type: ignore[call-arg]


def test_health_response_rejects_wrong_model_ready_type() -> None:
    with pytest.raises(ValidationError):
        HealthResponse(status="ok", model_ready="not-a-bool")


# ------------------------------------------------------- ErrorResponse

def test_error_response_accepts_valid_payload() -> None:
    resp = ErrorResponse(detail="boom", error_code="TIMEOUT", retryable=True)
    assert resp.detail == "boom"
    assert resp.error_code == "TIMEOUT"
    assert resp.retryable is True


def test_error_response_trace_id_defaults_to_none() -> None:
    resp = ErrorResponse(detail="boom", error_code="TIMEOUT", retryable=True)
    assert resp.trace_id is None


def test_error_response_rejects_missing_detail() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(error_code="TIMEOUT", retryable=True)  # type: ignore[call-arg]


def test_error_response_rejects_missing_retryable() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(detail="boom", error_code="TIMEOUT")  # type: ignore[call-arg]


def test_error_response_rejects_wrong_retryable_type() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(detail="boom", error_code="TIMEOUT", retryable="not-a-bool")


# ------------------------------------------------- Error taxonomy / AsrError

def test_taxonomy_status_and_retryable_codes() -> None:
    expected = {
        "AUDIO_INVALID": (400, False),
        "AUDIO_TOO_LARGE": (413, False),
        "VALIDATION": (422, False),
        "MODEL_NOT_READY": (503, True),
        "TIMEOUT": (504, True),
        "INFERENCE_FAILED": (500, True),
    }
    kinds = {
        "AUDIO_INVALID": AUDIO_INVALID,
        "AUDIO_TOO_LARGE": AUDIO_TOO_LARGE,
        "VALIDATION": VALIDATION,
        "MODEL_NOT_READY": MODEL_NOT_READY,
        "TIMEOUT": TIMEOUT,
        "INFERENCE_FAILED": INFERENCE_FAILED,
    }
    for code, (status, retryable) in expected.items():
        err = AsrError(kinds[code], f"detail for {code}")
        assert err.error_code == code
        assert err.status_code == status
        assert err.retryable is retryable
        assert err.detail == f"detail for {code}"


def test_asr_error_retryable_override() -> None:
    # The taxonomy default for INFERENCE_FAILED is retryable=True; a caller
    # may override per-instance via the retryable kwarg.
    err = AsrError(INFERENCE_FAILED, "oops", retryable=False)
    assert err.retryable is False
    assert err.status_code == 500


def test_build_error_response_shape() -> None:
    err = AsrError(TIMEOUT, "too slow")
    resp = build_error_response(err, "trace-123")
    assert isinstance(resp, ErrorResponse)
    assert resp.model_dump() == {
        "detail": "too slow",
        "error_code": "TIMEOUT",
        "retryable": True,
        "trace_id": "trace-123",
    }


def test_build_error_response_without_trace_id() -> None:
    err = AsrError(AUDIO_INVALID, "empty")
    resp = build_error_response(err)
    assert resp.trace_id is None
    assert resp.retryable is False
