"""HTTP contract tests for the ASR service via fastapi TestClient.

All tests inject a deterministic FakeRuntime through the documented DI seam
create_app(model_runtime=...); ASR_FAKE_MODEL is never used here.

Contract clauses under falsification:
- POST /v1/transcribe multipart "audio" -> 200 TranscribeResponse shape.
- Error taxonomy: VALIDATION 422 / AUDIO_INVALID 400 / AUDIO_TOO_LARGE 413 /
  MODEL_NOT_READY 503 / TIMEOUT 504 / INFERENCE_FAILED 500 with the documented
  retryable flags and an error body {detail, error_code, retryable, trace_id}.
- x-trace-id header on responses (echoes the client's value when provided).
- GET /health shape; GET / info keys.
"""

from __future__ import annotations

import pytest

from src.config import AsrConfig
from src.errors import (
    AUDIO_INVALID,
    INFERENCE_FAILED,
    MODEL_NOT_READY,
    TIMEOUT,
    AsrError,
)
from src.main import create_app
from src.schemas import TranscribeResponse

AUDIO_BYTES = b"RIFF\x00\x00\x00\x00WAVE fake audio payload for tests"


def _audio_files(payload: bytes = AUDIO_BYTES) -> dict:
    return {"audio": ("sample.wav", payload, "audio/wav")}


def _custom_config(max_audio_mb: int | None = None, timeout_s: float | None = None) -> AsrConfig:
    """Fresh config with selected overrides; never touches the get_config cache."""
    cfg = AsrConfig()
    if max_audio_mb is not None:
        cfg.max_audio_mb = max_audio_mb
    if timeout_s is not None:
        cfg.request_timeout_seconds = timeout_s
    return cfg


# ---------------------------------------------------------------- success

def test_transcribe_success_returns_contract_body(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "language", "text", "words", "timestamps",
        "duration_ms", "elapsed_ms", "model", "aligner",
    }
    assert body["language"] == "en"
    assert body["text"] == "Hello world from fake ASR."
    assert isinstance(body["words"], list) and len(body["words"]) == 5
    for word in body["words"]:
        assert set(word.keys()) == {"text", "start_ms", "end_ms"}
        assert isinstance(word["text"], str)
        assert isinstance(word["start_ms"], int)
        assert isinstance(word["end_ms"], int)
    assert body["timestamps"] is True
    assert body["duration_ms"] == 1500
    assert isinstance(body["elapsed_ms"], int) and body["elapsed_ms"] >= 0
    assert body["model"] == "fake-model"
    assert body["aligner"] == "fake-aligner"
    # The response is re-parseable by the documented response schema.
    TranscribeResponse(**body)


def test_transcribe_success_writes_audio_to_temp_file_with_suffix(
    client_factory, fake_runtime_factory
) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 200
    assert len(rt.transcribe_calls) == 1
    audio_path, language, max_new_tokens = rt.transcribe_calls[0]
    assert audio_path.endswith(".wav")
    assert language is None
    assert max_new_tokens == 256  # documented form default


def test_transcribe_passes_language_form_field(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(),
            data={"language": "id"},
        )
    assert resp.status_code == 200
    assert rt.transcribe_calls[0][1] == "id"


def test_transcribe_clamps_max_new_tokens_upper(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(),
            data={"max_new_tokens": "99999"},
        )
    assert resp.status_code == 200
    assert rt.transcribe_calls[0][2] == 2048


def test_transcribe_clamps_max_new_tokens_lower(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(),
            data={"max_new_tokens": "-5"},
        )
    assert resp.status_code == 200
    assert rt.transcribe_calls[0][2] == 1


def test_transcribe_lifespan_loads_runtime_once(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 200
    assert rt.load_calls == 1  # lifespan, not the _ensure_runtime fallback


def test_success_response_carries_x_trace_id(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(),
            headers={"x-trace-id": "client-supplied-trace"},
        )
    assert resp.status_code == 200
    assert resp.headers.get("x-trace-id") == "client-supplied-trace"


# -------------------------------------------------------------- validation

def test_missing_audio_file_returns_422_validation(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "VALIDATION"
    assert body["retryable"] is False
    assert isinstance(body["detail"], str) and body["detail"]
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert resp.headers.get("x-trace-id") == body["trace_id"]


def test_invalid_max_new_tokens_type_returns_422(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(),
            data={"max_new_tokens": "abc"},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "VALIDATION"
    assert body["retryable"] is False


# --------------------------------------------------------- audio_invalid

def test_empty_audio_returns_400_audio_invalid(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files(payload=b""))
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_code"] == "AUDIO_INVALID"
    assert body["retryable"] is False
    assert "empty" in body["detail"].lower()
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert resp.headers.get("x-trace-id") == body["trace_id"]


def test_empty_audio_echoes_client_trace_id(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(payload=b""),
            headers={"x-trace-id": "trace-abc-123"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["trace_id"] == "trace-abc-123"
    assert resp.headers.get("x-trace-id") == "trace-abc-123"


# -------------------------------------------------------- audio_too_large

def test_oversized_audio_returns_413(monkeypatch, client_factory, fake_runtime_factory) -> None:
    monkeypatch.setattr("src.main.get_config", lambda: _custom_config(max_audio_mb=1))
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(payload=b"x" * (1024 * 1024 + 1)),
        )
    assert resp.status_code == 413
    body = resp.json()
    assert body["error_code"] == "AUDIO_TOO_LARGE"
    assert body["retryable"] is False
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert resp.headers.get("x-trace-id") == body["trace_id"]
    assert rt.transcribe_calls == []  # oversized audio never reaches the runtime


def test_audio_exactly_at_limit_is_accepted(monkeypatch, client_factory, fake_runtime_factory) -> None:
    monkeypatch.setattr("src.main.get_config", lambda: _custom_config(max_audio_mb=1))
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.post(
            "/v1/transcribe",
            files=_audio_files(payload=b"x" * (1024 * 1024)),
        )
    assert resp.status_code == 200
    assert len(rt.transcribe_calls) == 1


# ---------------------------------------------------------------- timeout

def test_inference_wall_clock_timeout_returns_504(
    monkeypatch, client_factory, fake_runtime_factory
) -> None:
    monkeypatch.setattr(
        "src.main.get_config", lambda: _custom_config(timeout_s=0.05)
    )
    rt = fake_runtime_factory(transcribe_delay_s=0.3)
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 504
    body = resp.json()
    assert body["error_code"] == "TIMEOUT"
    assert body["retryable"] is True
    assert "timed out" in body["detail"].lower()
    assert resp.headers.get("x-trace-id") == body["trace_id"]


def test_asr_error_timeout_maps_to_504(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory(transcribe_error=AsrError(TIMEOUT, "simulated timeout"))
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 504
    body = resp.json()
    assert body["error_code"] == "TIMEOUT"
    assert body["retryable"] is True
    assert resp.headers.get("x-trace-id") == body["trace_id"]


# --------------------------------------------------------- model_not_ready

def test_not_ready_runtime_returns_503(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory(ready=False)
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_code"] == "MODEL_NOT_READY"
    assert body["retryable"] is True
    assert resp.headers.get("x-trace-id") == body["trace_id"]


def test_asr_error_model_not_ready_maps_to_503(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory(
        transcribe_error=AsrError(MODEL_NOT_READY, "model reloading")
    )
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_code"] == "MODEL_NOT_READY"
    assert body["retryable"] is True


# --------------------------------------------------------- inference_failed

def test_asr_error_inference_failed_maps_to_500(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory(
        transcribe_error=AsrError(INFERENCE_FAILED, "gpu oom")
    )
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "INFERENCE_FAILED"
    assert body["retryable"] is True
    assert resp.headers.get("x-trace-id") == body["trace_id"]


def test_generic_runtime_exception_maps_to_500_inference_failed(
    client_factory, fake_runtime_factory
) -> None:
    # FastAPI routes the catch-all `@app.exception_handler(Exception)` to
    # Starlette's ServerErrorMiddleware, which sends the handler's response and
    # then ALWAYS re-raises the exception (starlette/middleware/errors.py:186).
    # The default TestClient (raise_server_exceptions=True) therefore surfaces
    # the exception to the caller instead of the response. We disable that flag
    # to observe the actual contract response produced by the app's handler.
    rt = fake_runtime_factory(transcribe_error=RuntimeError("boom"))
    with client_factory(rt, raise_server_exceptions=False) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "INFERENCE_FAILED"
    assert body["retryable"] is True
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    # The catch-all handler bypasses the http middleware, so the header is set
    # directly by the handler itself (main.py:188).
    assert resp.headers.get("x-trace-id") == body["trace_id"]


def test_asr_error_audio_invalid_from_runtime_maps_to_400(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory(
        transcribe_error=AsrError(AUDIO_INVALID, "unreadable audio stream")
    )
    with client_factory(rt) as client:
        resp = client.post("/v1/transcribe", files=_audio_files())
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_code"] == "AUDIO_INVALID"
    assert body["retryable"] is False


# ----------------------------------------------------------------- health

def test_health_ready_returns_200_shape(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory(status_payload={"gpu": "FAKE-GPU", "vram_free_mb": 8192})
    with client_factory(rt) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"status", "model_ready", "gpu", "vram_free_mb", "queue_depth"}
    assert body["status"] == "ok"
    assert body["model_ready"] is True
    assert body["gpu"] == "FAKE-GPU"
    assert body["vram_free_mb"] == 8192
    assert body["queue_depth"] == 0  # idle serial queue


def test_health_not_ready_reports_loading(client_factory, fake_runtime_factory) -> None:
    # A not-ready runtime has never loaded a GPU, so its status is empty —
    # mirroring ModelRuntime.status() before load().
    rt = fake_runtime_factory(
        ready=False, status_payload={"gpu": None, "vram_free_mb": None}
    )
    with client_factory(rt) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "loading"
    assert body["model_ready"] is False
    assert body["gpu"] is None
    assert body["vram_free_mb"] is None


# ------------------------------------------------------------------- root

def test_root_returns_info_keys(client_factory, fake_runtime_factory) -> None:
    rt = fake_runtime_factory()
    with client_factory(rt) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"name", "version", "docs", "api"}
    assert body["name"] == "SupoClip ASR Service"
    assert body["version"] == "1.0.0"
    assert body["docs"] == "/docs"
    assert body["api"] == "/v1/transcribe"


# ------------------------------------------------- app wiring sanity check

def test_app_module_exports_an_app_instance() -> None:
    from src.main import app

    assert app is not None
    assert app.title == "SupoClip ASR Service"
