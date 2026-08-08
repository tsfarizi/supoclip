"""Shared test doubles and fixtures for the ASR contract test suite.

FakeRuntime mirrors the *public* ModelRuntime contract
(load / is_ready / status / transcribe) with fully deterministic behavior
and no GPU/torch dependency. Every test builds its own instance through the
factory fixture — there is zero shared mutable state across tests.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.errors import AsrError
from src.main import create_app


class FakeRuntime:
    """Deterministic stand-in for ModelRuntime, controllable per test.

    Raises `transcribe_error` (if set) on every transcribe call; otherwise
    returns a fixed, contract-shaped payload. Optionally sleeps
    `transcribe_delay_s` before answering so timeout paths can be exercised.
    """

    def __init__(
        self,
        *,
        ready: bool = True,
        transcribe_error: AsrError | Exception | None = None,
        transcribe_delay_s: float = 0.0,
        status_payload: dict[str, Any] | None = None,
    ) -> None:
        self._ready = ready
        self._error = transcribe_error
        self._delay_s = transcribe_delay_s
        self._status = (
            {"gpu": "FAKE-GPU", "vram_free_mb": 8192}
            if status_payload is None
            else dict(status_payload)
        )
        self.load_calls = 0
        self.transcribe_calls: list[tuple[str, str | None, int]] = []

    def load(self) -> None:
        self.load_calls += 1

    @property
    def is_ready(self) -> bool:
        return self._ready

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        max_new_tokens: int = 256,
    ) -> dict[str, Any]:
        self.transcribe_calls.append((audio_path, language, max_new_tokens))
        if self._delay_s:
            time.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return {
            "language": "en",
            "text": "Hello world from fake ASR.",
            "words": [
                {"text": "Hello", "start_ms": 0, "end_ms": 300},
                {"text": "world", "start_ms": 300, "end_ms": 600},
                {"text": "from", "start_ms": 600, "end_ms": 900},
                {"text": "fake", "start_ms": 900, "end_ms": 1200},
                {"text": "ASR.", "start_ms": 1200, "end_ms": 1500},
            ],
            "timestamps": True,
            "duration_ms": 1500,
            "elapsed_ms": 0,
            "model": "fake-model",
            "aligner": "fake-aligner",
        }


@pytest.fixture
def fake_runtime_factory():
    """Return a fresh FakeRuntime per call; state is never shared across tests."""

    def _make(**kwargs: Any) -> FakeRuntime:
        return FakeRuntime(**kwargs)

    return _make


@pytest.fixture
def client_factory():
    """Build a TestClient around create_app with an injected runtime.

    The runtime is injected through the documented DI seam
    (create_app(model_runtime=...)); ASR_FAKE_MODEL is never used in HTTP tests.
    """

    def _make(runtime: FakeRuntime, *, raise_server_exceptions: bool = True) -> TestClient:
        return TestClient(
            create_app(model_runtime=runtime),
            raise_server_exceptions=raise_server_exceptions,
        )

    return _make
