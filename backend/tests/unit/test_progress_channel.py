"""
Falsification tests for T3: progress channel separation (contract P3).

Pinned contracts (from the test plan):
1. The DB `tasks.progress` column is a sparse checkpoint log only. Within
   process_task, `update_task_status` is called ONLY at checkpoints:
   start (queued/error -> processing), one per-clip checkpoint
   (processing -> processing), and the terminal completed CAS. Real-time
   ticks emitted by video_service are forwarded to the progress_callback
   channel and NEVER reach the task repository.
2. Ordering: before clip_ready_callback is invoked, the clip row is already
   committed - an independent session must observe it (commit before publish).
3. A rejected per-clip checkpoint CAS (the task left processing concurrently)
   must not crash the pipeline; the rejection is absorbed with a warning.
4. Frontend grep: frontend/src contains no merge-reconciliation heuristic
   (mergeIncremental / "never shrink" / "keep the longer") that could
   diverge from the DB-derived clip list.

The checkpoint test uses a fully mocked service (repos are AsyncMocks) so the
DB write count is observable; the ordering test uses the REAL repositories
against the test database so "commit before publish" is proven against actual
durability.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Config
from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from src.services import task_service as task_service_module
from src.services.task_service import TaskService
from tests.fixtures.factories import create_source, create_user

REPO_ROOT = Path(__file__).resolve().parents[3]


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


def _clip_info(idx: int) -> dict:
    return {
        "filename": f"clip-{idx}.mp4",
        "path": f"/tmp/clip-{idx}.mp4",
        "start_time": f"00:0{idx}",
        "end_time": "00:10",
        "duration": 10.0,
        "text": f"Clip {idx}",
        "relevance_score": 0.9,
        "reasoning": "reason",
        "virality_score": 1,
        "hook_score": 1,
        "engagement_score": 1,
        "value_score": 1,
        "shareability_score": 1,
        "hook_type": "hook",
        "hook_title": None,
    }


def _result_dict(segment_count: int = 2) -> dict:
    return {
        "clips": [_clip_info(i) for i in range(1, segment_count + 1)],
        "segments_to_render": [
            {"start_time": f"00:0{i}", "end_time": "00:10"}
            for i in range(segment_count)
        ],
        "video_path": "/tmp/source.mp4",
        "segments": [],
        "summary": None,
        "key_topics": [],
        "transcript": "Transcript",
        "analysis_json": "{}",
    }


class _UnconfiguredEmailService:
    """Stand-in for TaskCompletionEmailService that is never configured, so the
    completion notification path is skipped without touching the network."""

    def __init__(self, config):
        self.config = config
        self.is_configured = False


def _build_mocked_service(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=AsyncMock(), config=config)
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)
    service.clip_repo.create_clip = AsyncMock(side_effect=["clip-1", "clip-2"])
    service.video_service.create_single_clip = AsyncMock(
        side_effect=[_clip_info(1), _clip_info(2)]
    )
    return service


# ---------------------------------------------------------------------------
# 1. DB status writes happen ONLY on checkpoints, never per tick
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_status_writes_only_on_checkpoints_not_per_tick(tmp_path):
    """P3 clause 1: ticks emitted by video_service are forwarded to the
    real-time progress_callback; update_task_status is called only at the
    pinned checkpoints (start + per-clip + completed) with matching CAS
    guards, never with a tick percent."""
    service = _build_mocked_service(tmp_path)
    status_calls = []

    async def record_status(*args, **kwargs):
        status = kwargs.get("status", args[2] if len(args) > 2 else None)
        status_calls.append(
            {
                "status": status,
                "expected": list(kwargs["expected_statuses"]),
                "progress": kwargs.get("progress"),
            }
        )
        return True

    service.task_repo.update_task_status = AsyncMock(side_effect=record_status)

    tick_percents = [10, 20, 30, 40, 50, 60]
    progress_spy = []

    async def tick_sender(**kwargs):
        pcb = kwargs.get("progress_callback")
        for percent in tick_percents:
            if pcb:
                await pcb(percent, f"tick {percent}")
        return _result_dict()

    service.video_service.process_video_complete = AsyncMock(side_effect=tick_sender)

    async def progress_callback(percent: int, message: str, status: str = "processing"):
        progress_spy.append((percent, message, status))

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
        progress_callback=progress_callback,
    )

    assert result["clips_count"] == 2

    # Real-time channel: every tick reached the progress_callback spy.
    spy_tick_percents = [p for (p, _m, _s) in progress_spy if p in tick_percents]
    assert spy_tick_percents == tick_percents

    # DB status writes are ONLY checkpoints: start(0) + 2 per-clip + completed.
    assert len(status_calls) == 4
    assert [c["status"] for c in status_calls] == [
        "processing",
        "processing",
        "processing",
        "completed",
    ]
    assert [c["expected"] for c in status_calls] == [
        ["queued", "error"],
        ["processing"],
        ["processing"],
        ["processing"],
    ]
    db_progress_values = [c["progress"] for c in status_calls]
    assert db_progress_values == [0, 82, 95, 100]
    assert set(tick_percents).isdisjoint(set(db_progress_values)), (
        "a video tick percent leaked into a DB status write"
    )


# ---------------------------------------------------------------------------
# 2. clip_ready_callback observes a committed row (commit before publish)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_row_is_committed_before_ready_publish(
    db_session, initialized_database, tmp_path, monkeypatch
):
    """P3 clause 2: when clip_ready_callback runs, an INDEPENDENT session can
    already read the clip row - create_clip committed before the publish."""
    user_id = _uid("usr")
    await create_user(db_session, user_id=user_id, email=f"{user_id}@example.com")
    source_id = _uid("src")
    await create_source(db_session, source_id=source_id, title="Progress source")
    task_id = await TaskRepository.create_task(
        db_session, user_id=user_id, source_id=source_id, status="queued"
    )
    cache_key = TaskService._build_cache_key(
        "https://www.youtube.com/watch?v=demo", "youtube", "fast", False, 0
    )
    try:
        monkeypatch.setattr(
            task_service_module,
            "TaskCompletionEmailService",
            _UnconfiguredEmailService,
        )
        config = Config()
        config.temp_dir = str(tmp_path)
        service = TaskService(db=db_session, config=config)
        service.video_service.process_video_complete = AsyncMock(
            return_value=_result_dict(segment_count=2)
        )
        service.video_service.create_single_clip = AsyncMock(
            side_effect=[_clip_info(1), _clip_info(2)]
        )

        maker = async_sessionmaker(
            initialized_database, class_=AsyncSession, expire_on_commit=False
        )
        observed: list[tuple[int, bool, str]] = []

        async def clip_ready_callback(
            clip_index: int, total_clips: int, clip_data: dict
        ):
            # The observer is the SSE/publish boundary: it reads from its own
            # session. The row must already be durable (create_clip committed).
            async with maker() as fresh_session:
                row = await ClipRepository.get_clip_by_id(
                    fresh_session, clip_data["id"]
                )
                observed.append(
                    (
                        clip_index,
                        row is not None,
                        row["file_path"] if row else clip_data.get("file_path", ""),
                    )
                )

        result = await service.process_task(
            task_id=task_id,
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
            clip_ready_callback=clip_ready_callback,
        )

        assert result["clips_count"] == 2
        assert len(observed) == 2
        assert [idx for (idx, _ok, _p) in observed] == [0, 1]
        for _idx, ok, file_path in observed:
            assert ok, f"clip row not visible to a fresh session at publish time"
            assert file_path.startswith("/tmp/clip-")
        # Terminal state reached; the completed CAS won.
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "completed"
    finally:
        await db_session.execute(
            text("DELETE FROM processing_cache WHERE cache_key = :key"),
            {"key": cache_key},
        )
        await db_session.execute(
            text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id}
        )
        await db_session.execute(
            text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
        )
        await db_session.commit()


# ---------------------------------------------------------------------------
# 3. Rejected per-clip checkpoint CAS is absorbed with a warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_per_clip_checkpoint_cas_does_not_crash(tmp_path, caplog):
    """P3 clause 3: when the per-clip checkpoint CAS is rejected (the task
    left processing concurrently), the pipeline continues to the terminal
    completion without crashing and logs a warning."""
    service = _build_mocked_service(tmp_path)
    service.video_service.process_video_complete = AsyncMock(
        return_value=_result_dict(segment_count=2)
    )
    status_calls = []

    async def guarded_status(*args, **kwargs):
        status = kwargs.get("status", args[2] if len(args) > 2 else None)
        expected = list(kwargs["expected_statuses"])
        status_calls.append((status, tuple(expected)))
        if status == "processing" and expected == ["processing"]:
            # Per-clip checkpoint loses the race; completion CAS still wins.
            return False
        return True

    service.task_repo.update_task_status = AsyncMock(side_effect=guarded_status)

    with caplog.at_level(logging.WARNING, logger="src.services.task_service"):
        result = await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
        )

    assert result["clips_count"] == 2
    assert status_calls[-1] == ("completed", ("processing",))
    assert "Per-clip checkpoint rejected" in caplog.text


# ---------------------------------------------------------------------------
# 4. Frontend: no merge-reconciliation heuristic that could diverge
# ---------------------------------------------------------------------------


def test_frontend_has_no_merge_reconciliation_heuristic():
    """P3 clause 4 (grep assert): frontend/src must not contain
    mergeIncremental / "never shrink" / "keep the longer" - the clip list is
    derived exclusively from the backend (generated_clips), never reconciled
    client-side."""
    forbidden = ("mergeIncremental", "never shrink", "keep the longer")
    frontend_src = REPO_ROOT / "frontend" / "src"
    assert frontend_src.is_dir(), f"frontend/src not found at {frontend_src}"
    hits: list[tuple[str, str]] = []
    for path in frontend_src.rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
            content = path.read_text(encoding="utf-8", errors="replace")
            for token in forbidden:
                if token in content:
                    hits.append((str(path), token))
    assert not hits, f"frontend merge-reconciliation heuristic present: {hits}"