from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from src.errors import (
    AnalysisError,
    CancelledError,
    DownloadError,
    RenderError,
    TranscriptionError,
)
from src.services import task_service as task_service_module
from src.services.task_service import TaskService
from src.workers.tasks import sweep_stale_queued_tasks


@pytest.mark.asyncio
async def test_create_task_with_source_creates_queued_task(monkeypatch):
    service = TaskService(db=AsyncMock())
    service.task_repo.user_exists = AsyncMock(return_value=True)
    service.source_repo.create_source = AsyncMock(return_value="source-1")
    service.task_repo.create_task = AsyncMock(return_value="task-1")
    monkeypatch.setattr(
        service.video_service,
        "determine_source_type",
        lambda _url: "youtube",
    )
    service.video_service.get_video_title = AsyncMock(return_value="Seeded title")

    task_id = await service.create_task_with_source(
        user_id="user-1",
        url="https://www.youtube.com/watch?v=demo",
    )

    assert task_id == "task-1"
    service.task_repo.create_task.assert_awaited_once()

@pytest.mark.asyncio
async def test_create_task_with_source_requires_existing_user():
    service = TaskService(db=AsyncMock())
    service.task_repo.user_exists = AsyncMock(return_value=False)

    with pytest.raises(ValueError):
        await service.create_task_with_source(
            user_id="missing-user",
            url="https://example.com/video.mp4",
        )


def build_clip_result() -> dict:
    return {
        "filename": "clip-1.mp4",
        "path": "/tmp/clip-1.mp4",
        "start_time": "00:00",
        "end_time": "00:10",
        "duration": 10.0,
        "text": "Hook text",
        "relevance_score": 0.95,
        "reasoning": "Strong hook",
    }


def build_task_service() -> TaskService:
    config = Config()
    config.app_base_url = "http://localhost:3107"
    config.aws_region = "us-east-1"
    config.aws_access_key_id = "AKIATEST"
    config.aws_secret_access_key = "secret-test"
    config.ses_from_email = "SupoClip <noreply@example.com>"
    service = TaskService(db=AsyncMock(), config=config)
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="clip-1")
    service.video_service.create_single_clip = AsyncMock(return_value=build_clip_result())
    service.video_service.apply_single_transition = AsyncMock(
        side_effect=lambda _prev_clip_path, clip_info, _index, _clips_output_dir: clip_info
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [build_clip_result()],
            "segments_to_render": [{"start": 0, "end": 10}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )
    return service


@pytest.mark.parametrize(
    "url,source_type,processing_mode,include_broll",
    [
        ("https://www.youtube.com/watch?v=demo", "youtube", "fast", False),
        ("https://www.youtube.com/watch?v=demo", "youtube", "fast", True),
        ("https://youtu.be/demo", "youtube", "quality", False),
        ("upload://demo.mp4", "upload", "fast", False),
        ("  https://www.youtube.com/watch?v=demo  ", "youtube", "fast", False),
    ],
)
def test_cache_key_freezes_payload_ordering_and_broll_flag(
    url, source_type, processing_mode, include_broll
):
    cache_key = TaskService._build_cache_key(
        url,
        source_type,
        processing_mode,
        include_broll,
    )
    expected = hashlib.sha256(
        (
            f"{source_type}|{processing_mode}|{int(include_broll)}|"
            f"0|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url.strip()}"
        ).encode("utf-8")
    ).hexdigest()

    assert cache_key == expected


@pytest.mark.asyncio
async def test_update_clip_captions_passes_stored_task_style(monkeypatch, tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=AsyncMock(), config=config)
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"clip")
    output_path = tmp_path / "clips" / "edited.mp4"
    output_path.parent.mkdir()
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(input_path),
        "start_time": "00:10",
        "end_time": "00:12",
        "duration": 2.0,
    }
    service.clip_repo.get_clip_by_id = AsyncMock(
        side_effect=[clip, {**clip, "file_path": str(output_path)}]
    )
    service.clip_repo.update_clip = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={
            "id": "task-1",
            "source_url": "upload://source.mp4",
            "source_type": "upload",
            "processing_mode": "quality",
            "font_family": "Inter",
            "font_size": 48,
            "font_color": "#123456",
            "caption_template": "minimal",
        }
    )
    service.cache_repo.get_cache = AsyncMock(
        return_value={"video_path": str(tmp_path / "source.mp4")}
    )
    captured = {}

    def fake_overlay(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"edited")
        return output_path

    monkeypatch.setattr(task_service_module, "overlay_custom_captions", fake_overlay)

    await service.update_clip_captions(
        "task-1", "clip-1", "edited caption", "middle", ["edited"]
    )

    assert captured["kwargs"] == {
        "font_family": "Inter",
        "font_size": 48,
        "font_color": "#123456",
        "caption_template": "minimal",
        "transcript_video_path": tmp_path / "source.mp4",
        "source_ranges": [(10.0, 12.0)],
        "output_format": "vertical",
        "hook_persist": False,
        "hook_title": None,
        "watermark": None,
        "watermark_persist": False,
        "progress_callback": captured["kwargs"]["progress_callback"],
    }
    assert callable(captured["kwargs"]["progress_callback"])


@pytest.mark.asyncio
async def test_process_task_fails_when_no_clip_segments_are_selected():
    service = build_task_service()
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": '{"most_relevant_segments":[]}',
        }
    )

    with pytest.raises(ValueError, match="No usable clip segments"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    service.cache_repo.upsert_cache.assert_awaited_once()
    assert service.cache_repo.upsert_cache.await_args.kwargs["analysis_json"] is None
    service.task_repo.update_task_status.assert_any_await(
        service.db,
        "task-1",
        "error",
        expected_statuses=["queued", "processing"],
        progress=0,
        progress_message="No usable clip segments were selected for this video.",
    )
    service.clip_repo.create_clip.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_sends_completion_email_when_enabled(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": True,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock(return_value={"id": "email-1"})

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    send_task_completed_email.assert_awaited_once()
    service.task_repo.mark_completion_notification_sent.assert_awaited_once_with(
        service.db, "task-1"
    )


@pytest.mark.asyncio
async def test_process_task_skips_completion_email_when_disabled(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": False,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock()

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    send_task_completed_email.assert_not_awaited()
    service.task_repo.mark_completion_notification_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_keeps_generated_clips_standalone():
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": False,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.video_service.create_single_clip = AsyncMock(
        side_effect=[
            {
                **build_clip_result(),
                "filename": "clip-1.mp4",
                "path": "/tmp/clip-1.mp4",
                "duration": 10.0,
            },
            {
                **build_clip_result(),
                "filename": "clip-2.mp4",
                "path": "/tmp/clip-2.mp4",
                "start_time": "00:10",
                "end_time": "00:20",
                "duration": 10.0,
            },
        ]
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [build_clip_result(), build_clip_result()],
            "segments_to_render": [
                {"start_time": "00:00", "end_time": "00:10"},
                {"start_time": "00:10", "end_time": "00:20"},
            ],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 2
    service.video_service.apply_single_transition.assert_not_awaited()
    saved_paths = [
        call.kwargs["file_path"]
        for call in service.clip_repo.create_clip.await_args_list
    ]
    assert saved_paths == ["/tmp/clip-1.mp4", "/tmp/clip-2.mp4"]


# ---------------------------------------------------------------------------
# Falsification: process_task must NOT mark a task "completed" when every
# clip render fails (0 clips created). Contract: all create_single_clip
# calls returning None must surface as RenderError so the existing handler
# persists status "error" (error_code "task_error") with a render/clip
# failure message. Current code violates this: after the render loop it
# unconditionally writes "completed" even with an empty clip_ids list.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_all_clip_renders_fail_marks_error_not_completed():
    service = build_task_service()
    # Every render attempt fails: create_single_clip returns None for each of
    # the 2 segments, so clip_ids stays empty.
    service.video_service.create_single_clip = AsyncMock(return_value=None)
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [
                {"start": 0, "end": 10},
                {"start": 10, "end": 20},
            ],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )
    # No notification context: keeps the buggy completion path from reaching
    # the email service, so the RED failure is exactly the contract breach
    # (status "completed" with 0 clips), not repository noise.
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)

    status_calls = []

    async def record_status(*_args, **_kwargs):
        status_calls.append(
            _kwargs.get("status", _args[2] if len(_args) > 2 else None)
        )
        # CAS contract: every status write in this scenario wins, so the
        # terminal "error" transition is accepted and the error_code metadata
        # write that follows it actually happens.
        return True

    service.task_repo.update_task_status = AsyncMock(side_effect=record_status)

    # A zero-clip outcome is a render failure: it must raise RenderError so
    # the handler persists status "error" instead of "completed".
    with pytest.raises(RenderError):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    # (a) "completed" must never be persisted for a task with 0 clips.
    assert "completed" not in status_calls
    # (b) The last persisted status is "error" (via the handler), with a
    # progress_message that names the render/clip failure.
    assert status_calls[-1] == "error"
    last_status_call = service.task_repo.update_task_status.await_args_list[-1]
    last_message = last_status_call.kwargs["progress_message"].lower()
    assert "render" in last_message or "fail" in last_message
    # (c) RenderError maps to error_code "task_error" on runtime metadata.
    error_code_calls = [
        call.kwargs.get("error_code")
        for call in service.task_repo.update_task_runtime_metadata.await_args_list
        if "error_code" in call.kwargs
    ]
    assert error_code_calls == ["task_error"]
    # (d) Zero clips were persisted.
    service.clip_repo.create_clip.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_ignores_completion_email_failures(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": True,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock(side_effect=RuntimeError("email failed"))

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    send_task_completed_email.assert_awaited_once()
    service.task_repo.mark_completion_notification_sent.assert_not_awaited()
    assert any(
        call.kwargs.get("completed_at") is not None
        for call in service.task_repo.update_task_runtime_metadata.await_args_list
    )


@pytest.mark.asyncio
async def test_process_task_skips_completion_email_when_already_sent(monkeypatch):
    service = build_task_service()
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": True,
            "completion_notification_sent_at": datetime.now(timezone.utc),
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    service.task_repo.mark_completion_notification_sent = AsyncMock(return_value=True)
    send_task_completed_email = AsyncMock()

    class FakeTaskCompletionEmailService:
        def __init__(self, config):
            self.config = config

        @property
        def is_configured(self) -> bool:
            return True

        async def send_task_completed_email(self, **kwargs):
            return await send_task_completed_email(**kwargs)

    monkeypatch.setattr(
        task_service_module,
        "TaskCompletionEmailService",
        FakeTaskCompletionEmailService,
    )

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    send_task_completed_email.assert_not_awaited()
    service.task_repo.mark_completion_notification_sent.assert_not_awaited()


# ---------------------------------------------------------------------------
# Falsification: process_task error_code mapping (A11 contract, typed
# taxonomy). error_code derives from the exception TYPE, never from message
# text: DownloadError -> download_error, TranscriptionError ->
# transcription_error, AnalysisError -> analysis_error, RenderError ->
# task_error, CancelledError -> cancelled (with no error_code written). A
# generic exception - whatever its message - freezes to task_error; the
# string-matching fallback was removed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_type,message,expected_status,expected_progress_message,expected_error_code",
    [
        # Typed taxonomy: each typed pipeline failure maps by type.
        (
            DownloadError,
            "Failed to download video",
            "error",
            "Failed to download video",
            "download_error",
        ),
        (
            TranscriptionError,
            "Transcript generation failed",
            "error",
            "Transcript generation failed",
            "transcription_error",
        ),
        (
            AnalysisError,
            "AI transcript analysis failed",
            "error",
            "AI transcript analysis failed",
            "analysis_error",
        ),
        (
            RenderError,
            "Clip rendering failed for all segments",
            "error",
            "Clip rendering failed for all segments",
            "task_error",
        ),
        (CancelledError, "Task cancelled", "cancelled", "Cancelled by user", None),
        # Generic exceptions: the message text must NEVER influence error_code.
        # These are the messages the removed string-matching fallback used to
        # guess from; every one of them must now freeze to task_error.
        (
            RuntimeError,
            "Something entirely unexpected",
            "error",
            "Something entirely unexpected",
            "task_error",
        ),
        (
            RuntimeError,
            "Failed to download video",
            "error",
            "Failed to download video",
            "task_error",
        ),
        (
            RuntimeError,
            "YouTube video could not be fetched",
            "error",
            "YouTube video could not be fetched",
            "task_error",
        ),
        (
            RuntimeError,
            "Transcript generation failed",
            "error",
            "Transcript generation failed",
            "task_error",
        ),
        (
            RuntimeError,
            "Analysis step failed",
            "error",
            "Analysis step failed",
            "task_error",
        ),
        (
            RuntimeError,
            "User cancelled the operation",
            "error",
            "User cancelled the operation",
            "task_error",
        ),
        (
            RuntimeError,
            "download and analysis both failed",
            "error",
            "download and analysis both failed",
            "task_error",
        ),
        (
            RuntimeError,
            "analysis failed while generating transcript",
            "error",
            "analysis failed while generating transcript",
            "task_error",
        ),
        (
            RuntimeError,
            "transcript generation cancelled",
            "error",
            "transcript generation cancelled",
            "task_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_process_task_error_code_mapping_is_frozen(
    exc_type,
    message,
    expected_status,
    expected_progress_message,
    expected_error_code,
):
    service = build_task_service()
    service.video_service.process_video_complete = AsyncMock(
        side_effect=exc_type(message)
    )

    with pytest.raises(exc_type, match=re.escape(message)):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    # Terminal status carries the raw message except on the CancelledError
    # branch, which persists the fixed "Cancelled by user" message and never
    # writes an error_code.
    service.task_repo.update_task_status.assert_any_await(
        service.db,
        "task-1",
        expected_status,
        expected_statuses=["queued", "processing"],
        progress=0,
        progress_message=expected_progress_message,
    )
    error_code_calls = [
        call.kwargs.get("error_code")
        for call in service.task_repo.update_task_runtime_metadata.await_args_list
        if "error_code" in call.kwargs
    ]
    assert error_code_calls == (
        [expected_error_code] if expected_error_code is not None else []
    )


# ---------------------------------------------------------------------------
# Characterization: process_task cancellation path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_cancel_sets_cancelled_status_and_reraises(tmp_path):
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)

    async def should_cancel():
        return True

    with pytest.raises(Exception, match="Task cancelled"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
            should_cancel=should_cancel,
        )

    service.task_repo.update_task_status.assert_any_await(
        service.db,
        "task-1",
        "cancelled",
        expected_statuses=["queued", "processing"],
        progress=0,
        progress_message="Cancelled by user",
    )
    # The cancellation branch must NOT write an error_code to runtime metadata.
    error_code_calls = [
        call
        for call in service.task_repo.update_task_runtime_metadata.await_args_list
        if "error_code" in call.kwargs
    ]
    assert error_code_calls == []


# ---------------------------------------------------------------------------
# Stale-queued recovery: GET /tasks/{id} is read-only; the queued->error
# transition belongs to the worker recovery sweep (R4).
# ---------------------------------------------------------------------------


def _build_stale_config(timeout_seconds: int = 180) -> Config:
    config = Config()
    config.queued_task_timeout_seconds = timeout_seconds
    return config


def _is_stale_config_test_service(timeout_seconds: int) -> TaskService:
    return TaskService(db=None, config=_build_stale_config(timeout_seconds))


def test_is_stale_queued_task_boundary(monkeypatch):
    now = datetime.now(timezone.utc)

    # age >= timeout is stale (>=, not >)
    stale_service = _is_stale_config_test_service(timeout_seconds=0)
    assert (
        stale_service._is_stale_queued_task(
            {
                "status": "queued",
                "created_at": now,
                "updated_at": now - timedelta(seconds=1),
            }
        )
        is True
    )

    # age < timeout is not stale
    fresh_service = _is_stale_config_test_service(timeout_seconds=180)
    assert (
        fresh_service._is_stale_queued_task(
            {"status": "queued", "created_at": now, "updated_at": now}
        )
        is False
    )


def test_is_stale_queued_task_ignores_non_queued_and_missing_timestamps():
    service = _is_stale_config_test_service(timeout_seconds=0)
    now = datetime.now(timezone.utc)

    # non-queued status never stale regardless of age
    assert (
        service._is_stale_queued_task(
            {
                "status": "processing",
                "created_at": now - timedelta(seconds=9999),
                "updated_at": now - timedelta(seconds=9999),
            }
        )
        is False
    )
    # missing timestamps never stale, never crash
    assert service._is_stale_queued_task({"status": "queued"}) is False
    assert (
        service._is_stale_queued_task(
            {"status": "queued", "created_at": None, "updated_at": None}
        )
        is False
    )
    assert (
        service._is_stale_queued_task(
            {"status": "queued", "created_at": None, "updated_at": now}
        )
        is False
    )


@pytest.mark.asyncio
async def test_get_task_with_clips_is_read_only_for_stale_queued_task(monkeypatch):
    service = TaskService(
        db=AsyncMock(), config=_build_stale_config(timeout_seconds=180)
    )
    stale_task = {
        "id": "task-1",
        "status": "queued",
        "created_at": datetime.now(timezone.utc) - timedelta(seconds=200),
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=200),
    }
    service.task_repo.get_task_by_id = AsyncMock(return_value=stale_task)
    service.task_repo.update_task_status = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(
        return_value=[
            {"id": "clip-1", "file_path": "/tmp/clip-1.mp4", "text": "Hook"}
        ]
    )

    result = await service.get_task_with_clips("task-1")

    # The GET path never writes the stale-queued transition; the recovery
    # sweep owns it.
    service.task_repo.update_task_status.assert_not_awaited()
    service.task_repo.get_task_by_id.assert_awaited_once()
    assert result["status"] == "queued"
    assert result["clips_count"] == 1
    assert "file_path" not in result["clips"][0]
    # settings merged from source-settings defaults on top of the task row
    assert result["output_format"] == "vertical"
    assert result["add_subtitles"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task",
    [
        {"id": "task-1", "status": "queued", "created_at": None, "updated_at": None},
        {
            "id": "task-1",
            "status": "queued",
            "created_at": None,
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "id": "task-1",
            "status": "queued",
            "created_at": datetime.now(timezone.utc),
            "updated_at": None,
        },
        {
            "id": "task-1",
            "status": "queued",
            "created_at": datetime.now(timezone.utc) - timedelta(seconds=200),
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "id": "task-1",
            "status": "processing",
            "created_at": datetime.now(timezone.utc) - timedelta(seconds=200),
            "updated_at": datetime.now(timezone.utc) - timedelta(seconds=200),
        },
    ],
)
async def test_get_task_with_clips_no_timeout_transition(monkeypatch, task):
    service = TaskService(
        db=AsyncMock(), config=_build_stale_config(timeout_seconds=180)
    )
    service.task_repo.get_task_by_id = AsyncMock(return_value=task)
    service.task_repo.update_task_status = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])

    result = await service.get_task_with_clips("task-1")

    service.task_repo.update_task_status.assert_not_awaited()
    assert result["status"] == task["status"]
    assert result["clips_count"] == 0


class _FakeSession:
    pass


class _FakeSessionFactory:
    """Stand-in for AsyncSessionLocal: yields one fake session per enter."""

    def __init__(self):
        self.session = _FakeSession()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_sweep_stale_queued_tasks_marks_stale_as_error_and_skips_fresh(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    stale_task = {
        "id": "task-stale",
        "status": "queued",
        "created_at": now - timedelta(seconds=200),
        "updated_at": now - timedelta(seconds=200),
    }
    fresh_task = {
        "id": "task-fresh",
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    non_queued_task = {
        "id": "task-processing",
        "status": "processing",
        "created_at": now - timedelta(seconds=200),
        "updated_at": now - timedelta(seconds=200),
    }

    session_factory = _FakeSessionFactory()
    monkeypatch.setattr("src.database.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        "src.runtime_settings.load_runtime_settings_cache", AsyncMock()
    )
    monkeypatch.setattr(
        "src.repositories.task_repository.TaskRepository.get_queued_tasks",
        AsyncMock(return_value=[stale_task, fresh_task, non_queued_task]),
    )
    update_status_mock = AsyncMock()
    monkeypatch.setattr(
        "src.repositories.task_repository.TaskRepository.update_task_status",
        update_status_mock,
    )
    monkeypatch.setattr(
        "src.services.task_service.get_config",
        lambda: _build_stale_config(timeout_seconds=180),
    )

    result = await sweep_stale_queued_tasks({})

    assert result == 1
    update_status_mock.assert_awaited_once_with(
        session_factory.session,
        "task-stale",
        "error",
        expected_statuses=["queued"],
        progress=0,
        progress_message=(
            "Task timed out while waiting in queue. "
            "Ensure the worker process is running (run.ps1 starts it automatically)."
        ),
    )


# ---------------------------------------------------------------------------
# Characterization: trim_clip / split_clip / merge_clips with mocked editors
# (no ffmpeg). Source ranges fall back to start_time/end_time parsing.
# ---------------------------------------------------------------------------


def _build_editor_service(tmp_path) -> TaskService:
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=AsyncMock(), config=config)
    service.clip_repo.update_clip = AsyncMock()
    service.clip_repo.reorder_task_clips = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_trim_clip_updates_start_end_and_duration(monkeypatch, tmp_path):
    service = _build_editor_service(tmp_path)
    input_path = tmp_path / "clip-1.mp4"
    input_path.write_bytes(b"clip")
    output_dir = tmp_path / "clips"
    output_dir.mkdir()
    output_path = output_dir / "trimmed.mp4"
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(input_path),
        "start_time": "00:10",
        "end_time": "00:20",
        "duration": 10.0,
        "text": "Hook text",
    }
    service.clip_repo.get_clip_by_id = AsyncMock(
        side_effect=[clip, {**clip, "file_path": str(output_path)}]
    )
    monkeypatch.setattr(
        task_service_module,
        "trim_clip_file",
        lambda _input_path, _output_dir, start_offset, end_offset: output_path,
    )

    result = await service.trim_clip(
        "task-1", "clip-1", start_offset=2.0, end_offset=3.0
    )

    service.clip_repo.update_clip.assert_awaited_once_with(
        service.db,
        "clip-1",
        "trimmed.mp4",
        str(output_path),
        "00:12",
        "00:17",
        5.0,
        "Hook text",
    )
    assert result["file_path"] == str(output_path)


@pytest.mark.asyncio
async def test_split_clip_creates_second_clip(monkeypatch, tmp_path):
    service = _build_editor_service(tmp_path)
    input_path = tmp_path / "clip-1.mp4"
    input_path.write_bytes(b"clip")
    output_dir = tmp_path / "clips"
    output_dir.mkdir()
    first_path = output_dir / "split_a.mp4"
    second_path = output_dir / "split_b.mp4"
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(input_path),
        "start_time": "00:00",
        "end_time": "00:10",
        "duration": 10.0,
        "text": "Hook text",
        "clip_order": 2,
    }
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=clip)
    service.clip_repo.create_clip = AsyncMock(return_value="clip-2")
    monkeypatch.setattr(
        task_service_module,
        "split_clip_file",
        lambda _input_path, _output_dir, split_time: (first_path, second_path),
    )

    result = await service.split_clip("task-1", "clip-1", split_time=4.0)

    assert result == {"message": "Clip split successfully"}
    service.clip_repo.update_clip.assert_awaited_once_with(
        service.db,
        "clip-1",
        "split_a.mp4",
        str(first_path),
        "00:00",
        "00:04",
        4.0,
        "Hook text",
    )
    create_kwargs = service.clip_repo.create_clip.await_args.kwargs
    assert create_kwargs["task_id"] == "task-1"
    assert create_kwargs["filename"] == "split_b.mp4"
    assert create_kwargs["file_path"] == str(second_path)
    assert create_kwargs["start_time"] == "00:04"
    assert create_kwargs["end_time"] == "00:10"
    assert create_kwargs["duration"] == 6.0
    assert create_kwargs["text"] == "Hook text"
    assert create_kwargs["clip_order"] == 3
    service.clip_repo.reorder_task_clips.assert_awaited_once_with(
        service.db, "task-1"
    )


def _merge_clips_fixture(tmp_path, monkeypatch, transition=None):
    service = _build_editor_service(tmp_path)
    clip1 = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": "/tmp/clip-1.mp4",
        "start_time": "00:00",
        "end_time": "00:10",
        "duration": 10.0,
        "text": "First",
        "clip_order": 1,
    }
    clip2 = {
        "id": "clip-2",
        "task_id": "task-1",
        "file_path": "/tmp/clip-2.mp4",
        "start_time": "00:10",
        "end_time": "00:20",
        "duration": 10.0,
        "text": "Second",
        "clip_order": 2,
    }
    service.clip_repo.get_clip_by_id = AsyncMock(side_effect=[clip1, clip2])
    service.clip_repo.delete_clip = AsyncMock()
    output_dir = tmp_path / "clips"
    output_dir.mkdir()
    merged_path = output_dir / "merged.mp4"
    monkeypatch.setattr(
        task_service_module,
        "merge_clip_files",
        lambda _paths, _output_dir: merged_path,
    )
    return service, clip1, clip2, merged_path


@pytest.mark.asyncio
async def test_merge_clips_hard_concat_keeps_first_and_deletes_rest(
    monkeypatch, tmp_path
):
    service, _clip1, _clip2, merged_path = _merge_clips_fixture(tmp_path, monkeypatch)

    result = await service.merge_clips("task-1", ["clip-1", "clip-2"])

    service.clip_repo.update_clip.assert_awaited_once_with(
        service.db,
        "clip-1",
        "merged.mp4",
        str(merged_path),
        "00:00",
        "00:20",
        20.0,
        "First Second",
    )
    service.clip_repo.delete_clip.assert_awaited_once_with(service.db, "clip-2")
    service.clip_repo.reorder_task_clips.assert_awaited_once_with(
        service.db, "task-1"
    )
    assert result == {"message": "Clips merged successfully", "clip_id": "clip-1"}


@pytest.mark.asyncio
async def test_merge_clips_falls_back_to_hard_concat_when_transition_fails(
    monkeypatch, tmp_path
):
    service, _clip1, _clip2, merged_path = _merge_clips_fixture(tmp_path, monkeypatch)

    def failing_transition(_paths, _spec, _output_dir):
        raise RuntimeError("ffmpeg xfade failed")

    monkeypatch.setattr(
        task_service_module,
        "apply_transitions_between_clips",
        failing_transition,
    )

    result = await service.merge_clips(
        "task-1", ["clip-1", "clip-2"], transition="xfade:fade"
    )

    service.clip_repo.update_clip.assert_awaited_once_with(
        service.db,
        "clip-1",
        "merged.mp4",
        str(merged_path),
        "00:00",
        "00:20",
        20.0,
        "First Second",
    )
    assert result["clip_id"] == "clip-1"


@pytest.mark.asyncio
async def test_merge_clips_requires_at_least_two_clips():
    service = TaskService(db=AsyncMock())

    with pytest.raises(ValueError, match="At least two clips"):
        await service.merge_clips("task-1", ["clip-1"])


# ---------------------------------------------------------------------------
# Characterization: normalize_video_identity (duplicate-detection identity).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://youtu.be/AbC123", "youtube:AbC123"),
        ("https://www.youtube.com/watch?v=AbC123", "youtube:AbC123"),
        ("https://youtube.com/watch?v=AbC123", "youtube:AbC123"),
        ("https://www.youtube.com/watch?v=AbC123&t=30s", "youtube:AbC123"),
        ("https://youtu.be/AbC123?si=tracking_param", "youtube:AbC123"),
        ("  https://youtu.be/AbC123  ", "youtube:AbC123"),
        ("https://www.youtube.com/watch?v=AbC_12-xY", "youtube:AbC_12-xY"),
        ("upload://demo.mp4", "upload://demo.mp4"),
        ("https://example.com/video.mp4?x=1", "https://example.com/video.mp4?x=1"),
        # YouTube id shorter than 6 chars is not recognized -> verbatim
        ("https://youtu.be/short", "https://youtu.be/short"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_video_identity_vectors(url, expected):
    assert task_service_module.normalize_video_identity(url) == expected


# ---------------------------------------------------------------------------
# Characterization: find_active_task_for_source duplicate detection.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_active_task_for_source_matches_youtube_identity():
    service = TaskService(db=AsyncMock())
    active_tasks = [
        {
            "id": "task-1",
            "user_id": "user-1",
            "source_url": "https://www.youtube.com/watch?v=AbC123",
        },
        {
            "id": "task-2",
            "user_id": "user-2",
            "source_url": "upload://other.mp4",
        },
    ]
    service.task_repo.get_active_tasks_with_sources = AsyncMock(
        return_value=active_tasks
    )

    found = await service.find_active_task_for_source("https://youtu.be/AbC123")

    assert found == active_tasks[0]
    service.task_repo.get_active_tasks_with_sources.assert_awaited_once_with(
        service.db
    )


@pytest.mark.asyncio
async def test_find_active_task_for_source_matches_upload_verbatim():
    service = TaskService(db=AsyncMock())
    active_tasks = [
        {
            "id": "task-1",
            "user_id": "user-1",
            "source_url": "upload://demo.mp4",
        },
    ]
    service.task_repo.get_active_tasks_with_sources = AsyncMock(
        return_value=active_tasks
    )

    found = await service.find_active_task_for_source("upload://demo.mp4")

    assert found == active_tasks[0]


@pytest.mark.asyncio
async def test_find_active_task_for_source_returns_none_when_no_match():
    service = TaskService(db=AsyncMock())
    service.task_repo.get_active_tasks_with_sources = AsyncMock(
        return_value=[
            {
                "id": "task-1",
                "user_id": "user-1",
                "source_url": "https://www.youtube.com/watch?v=AbC123",
            }
        ]
    )

    found = await service.find_active_task_for_source("https://youtu.be/XyZ999")

    assert found is None


@pytest.mark.asyncio
async def test_find_active_task_for_source_empty_url_skips_query():
    service = TaskService(db=AsyncMock())
    service.task_repo.get_active_tasks_with_sources = AsyncMock()

    found = await service.find_active_task_for_source("")

    assert found is None
    service.task_repo.get_active_tasks_with_sources.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regression: process_task error paths must not leave an open/aborted
# transaction on the session (F1: Postgres `idle in transaction` leak).
# ---------------------------------------------------------------------------


class _FakeDbWithAbortedTransaction:
    """AsyncSession stand-in that records rollback and reports txn state.

    Starts inside an aborted transaction (the state left behind when the
    original pipeline error is itself a DB failure) so the error handler must
    roll back before writing the terminal status.
    """

    def __init__(self, in_transaction: bool = True):
        self.rollback_calls = 0
        self._in_transaction = in_transaction

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self._in_transaction = False


def _build_failing_service(db) -> TaskService:
    service = build_task_service()
    service.db = db
    service.video_service.process_video_complete = AsyncMock(
        side_effect=RuntimeError("simulated pipeline failure")
    )
    return service


@pytest.mark.asyncio
async def test_process_task_error_path_rolls_back_open_transaction():
    db = _FakeDbWithAbortedTransaction(in_transaction=True)
    service = _build_failing_service(db)

    with pytest.raises(RuntimeError, match="simulated pipeline failure"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    # The handler rolled the aborted transaction back before writing status...
    assert db.rollback_calls >= 1
    assert db.in_transaction() is False
    # ...and the status transition still happened.
    service.task_repo.update_task_status.assert_any_await(
        service.db,
        "task-1",
        "error",
        expected_statuses=["queued", "processing"],
        progress=0,
        progress_message="simulated pipeline failure",
    )


@pytest.mark.asyncio
async def test_process_task_error_path_rolls_back_even_when_clean():
    db = _FakeDbWithAbortedTransaction(in_transaction=False)
    service = _build_failing_service(db)

    with pytest.raises(RuntimeError, match="simulated pipeline failure"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    # Even on an already-clean session the handler rolls back before writing,
    # so the invariant "no open transaction" holds without relying on the
    # caller's close().
    assert db.rollback_calls >= 1
    assert db.in_transaction() is False
    service.task_repo.update_task_status.assert_any_await(
        service.db,
        "task-1",
        "error",
        expected_statuses=["queued", "processing"],
        progress=0,
        progress_message="simulated pipeline failure",
    )


@pytest.mark.asyncio
async def test_process_task_error_path_never_masks_original_when_status_write_fails():
    db = _FakeDbWithAbortedTransaction(in_transaction=True)
    service = _build_failing_service(db)

    # The first update_task_status (the "processing" transition inside the try
    # block) must succeed; only the error handler's status write may fail.
    status_calls = {"count": 0}

    async def flaky_status_write(*_args, **_kwargs):
        status_calls["count"] += 1
        if status_calls["count"] > 1:
            raise RuntimeError("status write exploded")
        # The first update_task_status (the "processing" transition) must win
        # the CAS so the pipeline actually runs and hits the simulated failure.
        return True

    service.task_repo.update_task_status = AsyncMock(side_effect=flaky_status_write)

    # The original pipeline error must propagate even though persisting the
    # terminal status failed; the session must still be rolled back clean.
    with pytest.raises(RuntimeError, match="simulated pipeline failure"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    assert db.rollback_calls >= 1
    assert db.in_transaction() is False


@pytest.mark.asyncio
async def test_process_video_task_worker_rolls_back_before_reraise(monkeypatch):
    """F1 regression at the worker boundary: when the pipeline fails,
    process_video_task must roll the session back before re-raising so no
    connection ever returns to the pool with an open transaction.

    The service-level rollback (see _persist_terminal_status) already runs in
    process_task; this test pins the worker's own defensive rollback too.
    """
    from src.workers.tasks import process_video_task

    class _WorkerFakeDb:
        def __init__(self):
            self.rollback_calls = 0
            self.closed = False

        def in_transaction(self) -> bool:
            return False

        async def rollback(self) -> None:
            self.rollback_calls += 1

        async def close(self) -> None:
            self.closed = True

    class _WorkerFakeSessionFactory:
        """Yields one fake session per `async with AsyncSessionLocal()`."""

        def __init__(self):
            self.db = _WorkerFakeDb()

        def __call__(self):
            return self

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_exc):
            await self.db.close()
            return False

    factory = _WorkerFakeSessionFactory()
    monkeypatch.setattr("src.database.AsyncSessionLocal", factory)
    monkeypatch.setattr(
        "src.runtime_settings.load_runtime_settings_cache", AsyncMock()
    )

    class _FakeTaskService:
        def __init__(self, db):
            self.db = db

        async def process_task(self, **_kwargs):
            raise RuntimeError("pipeline failed")

    monkeypatch.setattr(
        "src.services.task_service.TaskService", _FakeTaskService
    )

    class _CtxRedis:
        async def get(self, _key):
            return None

        async def set(self, *_args, **_kwargs):
            return True

        async def sadd(self, *_args, **_kwargs):
            return 1

        async def publish(self, *_args, **_kwargs):
            return 1

        async def setex(self, *_args, **_kwargs):
            return True

    with pytest.raises(RuntimeError, match="pipeline failed"):
        await process_video_task(
            {"redis": _CtxRedis(), "job_try": 1},
            "task-1",
            "upload://dummy.mp4",
            "video_url",
            "user-1",
        )

    assert factory.db.rollback_calls >= 1
    assert factory.db.closed is True
