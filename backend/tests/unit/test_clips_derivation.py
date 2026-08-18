"""
Falsification tests for T2: clip derivation from generated_clips (contract P1).

Pinned contracts (from the test plan):
1. `tasks.generated_clips_ids` was DROPPED; `get_task_by_id` never returns the
   key and the storage layer has no such column.
2. Clip membership is read exclusively from `generated_clips`:
   `get_clips_by_task` / `get_clips_count` are authoritative and mutually
   consistent, and `get_user_tasks().clips_count == len(get_clips_by_task)`.
3. Every edit flow (trim / split / merge / regenerate) mutates ONLY
   `generated_clips` rows; after the edit the reads stay consistent - there is
   no second path that could diverge.
4. Production code has zero references to the dropped array column or the
   removed `update_task_clips` helper (read semantics; grep assert).

The edit-flow tests exercise the REAL ClipRepository against the test
database and mock only the ffmpeg boundary functions (trim_clip_file /
split_clip_file / merge_clip_files), following the pattern of
test_clip_source_map / test_task_service.

Every DB test follows the suite convention: `asyncio(loop_scope="session")`,
seeds unique rows, and deletes them at the end.
"""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.config import Config
from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from src.services import task_service as task_service_module
from src.services.task_service import TaskService
from tests.fixtures.factories import create_source, create_user

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


async def _seed_user(db) -> str:
    user_id = _uid("usr")
    await create_user(db, user_id=user_id, email=f"{user_id}@example.com")
    return user_id


async def _seed_source(
    db,
    *,
    source_type: str = "youtube",
    url: str = "https://www.youtube.com/watch?v=seeded",
) -> str:
    source = await create_source(
        db, source_id=_uid("src"), title="Clip derivation source", source_type=source_type, url=url
    )
    return source["id"]


async def _seed_task(db, user_id: str, source_id: str, status: str = "completed") -> str:
    return await TaskRepository.create_task(
        db, user_id=user_id, source_id=source_id, status=status
    )


async def _cleanup(db, *, user_ids=(), source_ids=(), cache_keys=()):
    for key in cache_keys:
        await db.execute(
            text("DELETE FROM processing_cache WHERE cache_key = :key"), {"key": key}
        )
    for source_id in source_ids:
        await db.execute(
            text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id}
        )
    for user_id in user_ids:
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# 1. get_task_by_id has no generated_clips_ids key (column dropped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_get_task_by_id_has_no_generated_clips_ids_key(db_session):
    """P1 clause 1: the task dict returned by the read path must not contain
    the dropped array column."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task is not None
        assert "generated_clips_ids" not in task
        assert "clips_ids" not in task
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_tasks_table_has_no_generated_clips_ids_column(db_session):
    """P1 clause 1 (storage): the schema itself must have no
    tasks.generated_clips_ids column - reads cannot reference what does not
    exist."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'tasks' AND column_name = 'generated_clips_ids'
                """
            )
        )
    ).fetchall()
    assert rows == [], "tasks.generated_clips_ids column still exists in schema"


# ---------------------------------------------------------------------------
# 2. create_clip: get_clips_by_task / get_clips_count / get_user_tasks agree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_clip_reads_are_mutually_consistent(db_session):
    """P1 clause 2: after N create_clip calls, get_clips_by_task and
    get_clips_count agree, and get_user_tasks.clips_count equals the clip
    list length."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        for i in range(3):
            await ClipRepository.create_clip(
                db_session,
                task_id=task_id,
                filename=f"clip-{i + 1}.mp4",
                file_path=f"/tmp/clip-{i + 1}.mp4",
                start_time=f"00:0{i}",
                end_time="00:10",
                duration=10.0,
                text=f"Clip {i + 1}",
                relevance_score=0.9,
                reasoning="reason",
                clip_order=i + 1,
            )

        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        count = await ClipRepository.get_clips_count(db_session, task_id)
        user_tasks = await TaskRepository.get_user_tasks(db_session, user_id)

        assert len(clips) == 3
        assert count == 3
        assert [c["clip_order"] for c in clips] == [1, 2, 3]
        assert len(user_tasks) == 1
        assert user_tasks[0]["clips_count"] == len(clips) == count
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_task_reads_agree_on_zero(db_session):
    """P1 clause 2 (boundary): a task with no clips reads consistently as
    empty/zero across all three read surfaces."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        count = await ClipRepository.get_clips_count(db_session, task_id)
        user_tasks = await TaskRepository.get_user_tasks(db_session, user_id)
        assert clips == []
        assert count == 0
        assert user_tasks[0]["clips_count"] == 0
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# 3. Edit flows keep get_clips_by_task / get_clips_count consistent
# ---------------------------------------------------------------------------


def _build_service(db, tmp_path) -> TaskService:
    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=db, config=config)
    return service


async def _seed_clip_row(
    db, task_id: str, file_path: str, order: int, *, start: str = "00:00", end: str = "00:10"
) -> str:
    return await ClipRepository.create_clip(
        db,
        task_id=task_id,
        filename=f"clip-{order}.mp4",
        file_path=file_path,
        start_time=start,
        end_time=end,
        duration=10.0,
        text=f"Clip {order}",
        relevance_score=0.9,
        reasoning="reason",
        clip_order=order,
        virality_score=1,
        hook_score=1,
        engagement_score=1,
        value_score=1,
        shareability_score=1,
        hook_type="hook",
        hook_title=None,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_trim_edit_keeps_clip_reads_consistent(db_session, tmp_path, monkeypatch):
    """P1 clause 3 (trim): after a trim edit the single surviving row is
    updated in place; all read surfaces stay in agreement (no second path)."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    input_path = tmp_path / "clip-1.mp4"
    input_path.write_bytes(b"clip")
    try:
        clip_id = await _seed_clip_row(
            db_session, task_id, str(input_path), order=1
        )
        output_path = tmp_path / "clips" / "trimmed.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"trimmed")
        monkeypatch.setattr(
            task_service_module,
            "trim_clip_file",
            lambda _input_path, _output_dir, start_offset, end_offset: output_path,
        )

        service = _build_service(db_session, tmp_path)
        result = await service.trim_clip(task_id, clip_id, 2.0, 3.0)

        assert result["file_path"] == str(output_path)
        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        count = await ClipRepository.get_clips_count(db_session, task_id)
        user_tasks = await TaskRepository.get_user_tasks(db_session, user_id)
        assert len(clips) == 1
        assert count == 1
        assert clips[0]["id"] == clip_id
        assert user_tasks[0]["clips_count"] == len(clips) == count
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_split_edit_keeps_clip_reads_consistent(db_session, tmp_path, monkeypatch):
    """P1 clause 3 (split): a split edit adds a second generated_clips row and
    reorders; the reads agree on 2 clips."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    input_path = tmp_path / "clip-1.mp4"
    input_path.write_bytes(b"clip")
    try:
        clip_id = await _seed_clip_row(
            db_session, task_id, str(input_path), order=1
        )
        first_path = tmp_path / "clips" / "split_a.mp4"
        second_path = tmp_path / "clips" / "split_b.mp4"
        first_path.parent.mkdir(parents=True, exist_ok=True)
        first_path.write_bytes(b"a")
        second_path.write_bytes(b"b")
        monkeypatch.setattr(
            task_service_module,
            "split_clip_file",
            lambda _input_path, _output_dir, split_time: (first_path, second_path),
        )

        service = _build_service(db_session, tmp_path)
        result = await service.split_clip(task_id, clip_id, 4.0)

        assert result == {"message": "Clip split successfully"}
        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        count = await ClipRepository.get_clips_count(db_session, task_id)
        user_tasks = await TaskRepository.get_user_tasks(db_session, user_id)
        assert len(clips) == 2
        assert count == 2
        assert [c["clip_order"] for c in clips] == [1, 2]
        assert user_tasks[0]["clips_count"] == len(clips) == count
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_merge_edit_keeps_clip_reads_consistent(db_session, tmp_path, monkeypatch):
    """P1 clause 3 (merge): a merge edit collapses two generated_clips rows
    into one (first updated, second deleted); the reads agree on 1 clip."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    path1 = tmp_path / "clip-1.mp4"
    path2 = tmp_path / "clip-2.mp4"
    path1.write_bytes(b"1")
    path2.write_bytes(b"2")
    try:
        clip1 = await _seed_clip_row(
            db_session, task_id, str(path1), order=1,
            start="00:00", end="00:10",
        )
        clip2 = await _seed_clip_row(
            db_session, task_id, str(path2), order=2,
            start="00:10", end="00:20",
        )
        merged_path = tmp_path / "clips" / "merged.mp4"
        merged_path.parent.mkdir(parents=True, exist_ok=True)
        merged_path.write_bytes(b"merged")
        monkeypatch.setattr(
            task_service_module,
            "merge_clip_files",
            lambda _paths, _output_dir: merged_path,
        )

        service = _build_service(db_session, tmp_path)
        result = await service.merge_clips(task_id, [clip1, clip2])

        assert result["clip_id"] == clip1
        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        count = await ClipRepository.get_clips_count(db_session, task_id)
        user_tasks = await TaskRepository.get_user_tasks(db_session, user_id)
        assert len(clips) == 1
        assert count == 1
        assert clips[0]["id"] == clip1
        assert await ClipRepository.get_clip_by_id(db_session, clip2) is None
        assert user_tasks[0]["clips_count"] == len(clips) == count
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_regenerate_keeps_clip_reads_consistent(db_session, tmp_path, monkeypatch):
    """P1 clause 3 (regenerate): regenerating all clips replaces every
    generated_clips row with the re-rendered set; the reads agree on the new
    count (no leftover from the old set, no second path)."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(
        db_session,
        source_type="video_url",
        url="upload://source.mp4",
    )
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        for i in range(2):
            await _seed_clip_row(
                db_session, task_id, f"/tmp/old-clip-{i + 1}.mp4", order=i + 1
            )

        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(b"source")
        service = _build_service(db_session, tmp_path)

        new_infos = []
        for i in range(3):
            out = tmp_path / f"regenerated-{i + 1}.mp4"
            out.write_bytes(b"new")
            new_infos.append(
                {
                    "filename": out.name,
                    "path": str(out),
                    "start_time": f"00:0{i}",
                    "end_time": "00:10",
                    "duration": 10.0,
                    "text": f"Regenerated {i + 1}",
                    "relevance_score": 0.8,
                    "reasoning": "Regenerated with updated settings",
                }
            )
        # resolve_local_video_path is SYNC in production; an AsyncMock would
        # return a coroutine instead of a Path and crash the exists() check.
        service.video_service.resolve_local_video_path = lambda _url: source_path
        service.video_service.create_video_clips = AsyncMock(return_value=new_infos)

        await service.regenerate_all_clips_for_task(
            task_id,
            "TikTokSans-Regular",
            24,
            "#FFFFFF",
            "default",
            cleanup_settings={},
        )

        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        count = await ClipRepository.get_clips_count(db_session, task_id)
        user_tasks = await TaskRepository.get_user_tasks(db_session, user_id)
        assert len(clips) == 3
        assert count == 3
        assert [c["clip_order"] for c in clips] == [1, 2, 3]
        # every surviving row is from the regenerated set, none from the old set
        assert all("old-clip" not in c["file_path"] for c in clips)
        assert user_tasks[0]["clips_count"] == len(clips) == count
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# 4. Production read semantics: zero references to the dropped array column
# ---------------------------------------------------------------------------


def test_production_code_has_no_reference_to_dropped_array_column():
    """P1 clause 4 (grep assert): production code outside migrations must not
    reference tasks.generated_clips_ids or the removed update_task_clips
    helper - the only source of truth for clip membership is generated_clips."""
    forbidden = ("generated_clips_ids", "update_task_clips")
    src_root = BACKEND_ROOT / "src"
    hits: list[tuple[str, str]] = []
    for path in src_root.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in content:
                hits.append((str(path.relative_to(src_root)), token))
    assert not hits, f"production references to dropped clip-array paths: {hits}"