"""Falsification tests for the video upload size limit contract.

Pinned contract:
- Video upload limit is at least 2 GB = 2_147_483_648 bytes.
- Configurable via env ``MAX_VIDEO_UPLOAD_BYTES``, exposed as
  ``Config.max_video_upload_bytes`` (default ``2147483648``).
- The ``/upload`` route (``upload_video``) enforces the limit FROM config
  (not from any module-level constant) by passing it to
  ``_write_upload_to_disk(..., max_bytes)``.

The module-level ``MAX_VIDEO_UPLOAD_BYTES`` constant is deliberately NOT
asserted: it is dead code slated for removal, and the "limit >= 2 GB" clause
is covered by the ``Config`` default test and the route test.
"""

from pathlib import Path

import pytest
from starlette.requests import Request

from src.api.routes import media as media_module
from src.config import Config

CONTRACT_MIN_BYTES = 2 * 1024**3  # 2_147_483_648


def test_config_defaults_max_video_upload_bytes_to_at_least_2gb():
    """Contract: ``Config.max_video_upload_bytes`` defaults to >= 2 GiB."""
    assert Config().max_video_upload_bytes >= CONTRACT_MIN_BYTES


def test_config_reads_max_video_upload_bytes_from_env(monkeypatch):
    """Contract: ``MAX_VIDEO_UPLOAD_BYTES`` env var is honored by ``Config``.

    This is the environment-knob control: it proves the knob is wired
    independently of the >= 2 GiB default check. ``monkeypatch.setenv``
    restores the previous value (or removes the key) after the test, so no
    env contamination leaks between tests.
    """
    monkeypatch.setenv("MAX_VIDEO_UPLOAD_BYTES", "100000000")
    config = Config()
    assert config.max_video_upload_bytes == 100_000_000


@pytest.mark.asyncio
async def test_upload_route_enforces_configured_limit(monkeypatch, tmp_path):
    """Contract: ``upload_video`` passes the limit FROM config to
    ``_write_upload_to_disk``, never a module-level constant.

    The stub config carries a UNIQUE value (``CONTRACT_MIN_BYTES + 1``) that
    differs from any plausible module constant, so a green observation can
    only come from the route reading ``get_config().max_video_upload_bytes``.
    If the route regresses to a hardcoded constant, ``captured["max_bytes"]``
    will not equal the unique value -> RED mechanically.

    Design choice: instead of building a fragile full multipart request,
    ``get_config`` is stubbed and the fake ``_write_upload_to_disk`` records
    the ``max_bytes`` argument. This isolates the test from ambient env and
    pins the file writes to ``tmp_path``.
    """
    unique_configured_bytes = CONTRACT_MIN_BYTES + 1  # 2_147_483_649

    class StubConfig:
        temp_dir = str(tmp_path)
        max_video_upload_bytes = unique_configured_bytes

    captured = {}

    async def fake_write_upload_to_disk(uploaded_file, target_path, max_bytes):
        captured["max_bytes"] = max_bytes
        Path(target_path).write_bytes(b"dummy-upload")

    async def fake_get_authenticated_user_id(request, db):
        return "user-1"

    monkeypatch.setattr(media_module, "get_config", lambda: StubConfig())
    monkeypatch.setattr(media_module, "_write_upload_to_disk", fake_write_upload_to_disk)
    monkeypatch.setattr(
        media_module, "_get_authenticated_user_id", fake_get_authenticated_user_id
    )

    boundary = "----supoclip-test-boundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="video"; filename="clip.mp4"\r\n',
            b"Content-Type: video/mp4\r\n\r\n",
            b"fake-video-bytes",
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": [
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode())
        ],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, receive=receive)

    await media_module.upload_video(request, db=None)

    assert captured["max_bytes"] == unique_configured_bytes
