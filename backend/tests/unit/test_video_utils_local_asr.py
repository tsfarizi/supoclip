"""Offline contract tests for the local ASR transcript adapter.

Falsifies the documented contract of `get_video_transcript_local`:

- POST {asr_base_url}/v1/transcribe with a multipart "audio" field carrying
  the mono 16k mp3 produced by `_prepare_audio_for_transcription`.
- Retry: 3 attempts on httpx.TransportError and on any non-2xx EXCEPT
  400/413/422 (which fail immediately with RuntimeError); backoff 2s then 5s;
  final failure raises RuntimeError.
- Success: builds WordShim list from response words[] (start_ms/end_ms ->
  start/end in ms, confidence 1.0, speaker None), caches via
  cache_transcript_data (schema v2), returns
  "\\n".join(format_transcript_for_analysis(shim)).
- Degradation (words empty / timestamps false): warning logged, raw text
  cached, and the RAW transcript text (shim.text stripped) is returned —
  never the empty string.

Everything runs offline and deterministically: httpx.Client is patched at
src.video_utils.httpx (the module where the client is created),
_prepare_audio_for_transcription is stubbed to avoid ffmpeg, and time.sleep
is stubbed so backoff assertions are instant and exact.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

import src.video_utils as video_utils
from src.config import Config

SUCCESS_PAYLOAD = {
    "language": "en",
    "text": "Hello world. This is great!",
    "words": [
        {"text": "Hello", "start_ms": 0, "end_ms": 300},
        {"text": "world.", "start_ms": 300, "end_ms": 600},
        {"text": "This", "start_ms": 600, "end_ms": 900},
        {"text": "is", "start_ms": 900, "end_ms": 1200},
        {"text": "great!", "start_ms": 1200, "end_ms": 1500},
    ],
    "timestamps": True,
}

EXPECTED_FORMATTED = "[00:00 - 00:00] Hello world.\n[00:00 - 00:01] This is great!"

EXPECTED_CACHE_WORDS = [
    {"text": "Hello", "start": 0, "end": 300, "confidence": 1.0, "speaker": None},
    {"text": "world.", "start": 300, "end": 600, "confidence": 1.0, "speaker": None},
    {"text": "This", "start": 600, "end": 900, "confidence": 1.0, "speaker": None},
    {"text": "is", "start": 900, "end": 1200, "confidence": 1.0, "speaker": None},
    {"text": "great!", "start": 1200, "end": 1500, "confidence": 1.0, "speaker": None},
]


class FakeResponse:
    """Minimal httpx.Response stand-in exposing what the adapter touches."""

    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeHttpxClient:
    """Context-manager stand-in for httpx.Client that records POSTs.

    Each post() first consumes `errors` (exceptions to raise, e.g. transport
    failures), then `responses`. Exhausting both yields a 200 empty payload.
    """

    def __init__(self, responses: list | None = None, errors: list | None = None):
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.post_calls: list[dict] = []
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, files=None, **kwargs):
        self.post_calls.append({"url": url, "files": files})
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {"text": "", "words": [], "timestamps": True})


def _stub_env(monkeypatch, tmp_path, client: FakeHttpxClient) -> tuple[Path, list]:
    """Wire the offline harness: config, audio prep, httpx, sleep.

    Returns (audio_path, sleep_log) so tests can assert the multipart
    filename and the exact 2s/5s backoff sequence.
    """
    audio_path = tmp_path / "prepared.mp3"
    audio_path.write_bytes(b"fake-audio-bytes")

    config = Config()
    config.asr_base_url = "http://asr.test"
    config.assembly_ai_http_timeout_seconds = 30
    monkeypatch.setattr(video_utils, "get_config", lambda: config)
    monkeypatch.setattr(
        video_utils, "_prepare_audio_for_transcription", lambda _video_path: audio_path
    )

    def _client_factory(timeout=None):
        client.timeout = timeout
        return client

    monkeypatch.setattr(video_utils.httpx, "Client", _client_factory)

    sleep_log: list = []
    monkeypatch.setattr(video_utils.time, "sleep", lambda seconds: sleep_log.append(seconds))
    return audio_path, sleep_log


def _cache_payload(video_path: Path) -> dict:
    cache_path = video_path.with_suffix(".transcript_cache.json")
    return json.loads(cache_path.read_text())


# ------------------------------------------------------------- success path

def test_local_asr_success_returns_timestamped_segments_and_caches_schema_v2(
    monkeypatch, tmp_path
) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(responses=[FakeResponse(200, SUCCESS_PAYLOAD)])
    audio_path, sleep_log = _stub_env(monkeypatch, tmp_path, client)

    result = video_utils.get_video_transcript_local(video_path)

    # POST contract: {asr_base_url}/v1/transcribe, multipart "audio"
    # (filename from the prepared audio, audio/mpeg), configured timeout.
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["url"] == "http://asr.test/v1/transcribe"
    audio_name, _audio_file, audio_mime = call["files"]["audio"]
    assert audio_name == audio_path.name == "prepared.mp3"
    assert audio_mime == "audio/mpeg"
    assert client.timeout == 30

    # Return value: "\n".join(format_transcript_for_analysis(shim)).
    assert result == EXPECTED_FORMATTED
    assert sleep_log == []

    # Cache: schema v2 words with text/start/end/confidence/speaker.
    payload = _cache_payload(video_path)
    assert payload["version"] == video_utils.TRANSCRIPT_CACHE_SCHEMA_VERSION == 2
    assert payload["words"] == EXPECTED_CACHE_WORDS
    assert payload["text"] == SUCCESS_PAYLOAD["text"]
    assert payload["utterances"] == []


# ------------------------------------------------------------- degradation

@pytest.mark.parametrize("timestamps", [False, True])
def test_local_asr_empty_words_returns_raw_text_logs_warning_and_caches_raw_text(
    monkeypatch, tmp_path, caplog, timestamps
) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    payload = {
        "language": "en",
        "text": "  raw transcript text  ",
        "words": [],
        "timestamps": timestamps,
    }
    client = FakeHttpxClient(responses=[FakeResponse(200, payload)])
    _stub_env(monkeypatch, tmp_path, client)

    with caplog.at_level(logging.WARNING, logger="src.video_utils"):
        result = video_utils.get_video_transcript_local(video_path)

    # Degraded return is the RAW text (stripped), never "".
    assert result == "raw transcript text"
    assert any("no timestamps" in record.message for record in caplog.records)

    cache_payload = _cache_payload(video_path)
    assert cache_payload["version"] == video_utils.TRANSCRIPT_CACHE_SCHEMA_VERSION
    assert cache_payload["words"] == []
    assert cache_payload["text"] == "  raw transcript text  "


def test_local_asr_timestamps_false_with_words_logs_warning_and_caches_raw_text(
    monkeypatch, tmp_path, caplog
) -> None:
    """Timestamps=false with words present: degradation is signalled (warning)
    and the raw text is cached; the words still drive segment formatting."""
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    payload = {
        "language": "en",
        "text": "Hello world.",
        "words": [
            {"text": "Hello", "start_ms": 0, "end_ms": 300},
            {"text": "world.", "start_ms": 300, "end_ms": 600},
        ],
        "timestamps": False,
    }
    client = FakeHttpxClient(responses=[FakeResponse(200, payload)])
    _stub_env(monkeypatch, tmp_path, client)

    with caplog.at_level(logging.WARNING, logger="src.video_utils"):
        result = video_utils.get_video_transcript_local(video_path)

    assert any("no timestamps" in record.message for record in caplog.records)
    assert result == "[00:00 - 00:00] Hello world."
    cache_payload = _cache_payload(video_path)
    assert cache_payload["text"] == "Hello world."
    assert len(cache_payload["words"]) == 2


# ------------------------------------------------------------- retry: 5xx

def test_local_asr_retries_503_twice_then_succeeds(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(
        responses=[
            FakeResponse(503, text="busy"),
            FakeResponse(503, text="busy"),
            FakeResponse(200, SUCCESS_PAYLOAD),
        ]
    )
    _stub_env(monkeypatch, tmp_path, client)

    result = video_utils.get_video_transcript_local(video_path)

    assert result == EXPECTED_FORMATTED
    assert len(client.post_calls) == 3
    assert client.timeout == 30  # same configured timeout on every attempt


def test_local_asr_persistent_503_raises_runtime_error_after_three_attempts(
    monkeypatch, tmp_path
) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(responses=[FakeResponse(503, text="busy")] * 3)
    _stub_env(monkeypatch, tmp_path, client)

    with pytest.raises(RuntimeError, match="status 503"):
        video_utils.get_video_transcript_local(video_path)

    assert len(client.post_calls) == 3
    assert client.timeout == 30  # a client is created per attempt


def test_local_asr_backoff_is_2s_then_5s_on_retries(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(responses=[FakeResponse(503, text="busy")] * 3)
    audio_path, sleep_log = _stub_env(monkeypatch, tmp_path, client)

    with pytest.raises(RuntimeError, match="status 503"):
        video_utils.get_video_transcript_local(video_path)

    assert len(client.post_calls) == 3
    assert sleep_log == [2, 5]


# ------------------------------------------------------------- immediate 4xx

@pytest.mark.parametrize("status", [400, 413, 422])
def test_local_asr_client_error_fails_immediately_with_one_attempt(
    monkeypatch, tmp_path, status
) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(responses=[FakeResponse(status, text="nope")])
    audio_path, sleep_log = _stub_env(monkeypatch, tmp_path, client)

    with pytest.raises(RuntimeError, match=f"status {status}"):
        video_utils.get_video_transcript_local(video_path)

    assert len(client.post_calls) == 1
    assert sleep_log == []


# ------------------------------------------------------------- retry: transport errors

def test_local_asr_retries_transport_error_twice_then_succeeds(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(
        errors=[httpx.ConnectError("conn refused"), httpx.ConnectError("conn refused")],
        responses=[FakeResponse(200, SUCCESS_PAYLOAD)],
    )
    audio_path, sleep_log = _stub_env(monkeypatch, tmp_path, client)

    result = video_utils.get_video_transcript_local(video_path)

    assert result == EXPECTED_FORMATTED
    assert len(client.post_calls) == 3
    assert sleep_log == [2, 5]


def test_local_asr_persistent_transport_error_raises_runtime_error(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    client = FakeHttpxClient(errors=[httpx.ConnectError("conn refused")] * 3)
    audio_path, sleep_log = _stub_env(monkeypatch, tmp_path, client)

    with pytest.raises(RuntimeError, match="conn refused"):
        video_utils.get_video_transcript_local(video_path)

    assert len(client.post_calls) == 3
    assert sleep_log == [2, 5]
