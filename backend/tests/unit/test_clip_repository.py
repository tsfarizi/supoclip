"""
Characterization tests for ClipRepository.

Freeze the CURRENT database behavior of ClipRepository before any refactor.
Every test seeds unique rows and deletes them at the end.

CURRENT BEHAVIOR (frozen by test_create_clip_persists_and_returns_id):
ClipRepository.create_clip persists a row and returns its id. The A17 fix added
an explicit `id` (generate_uuid_string); the B3 fallback INSERT is removed, so
there is a single fail-loud INSERT. Schema v2 also gives generated_clips.id a
server default. The round-trip test verifies the returned id, the full
read-back payload, and that the row is visible to a NEW session without a
manual commit (the create path commits internally, matching
TaskRepository.create_task).

The read/update/delete/reorder tests seed clips via direct SQL INSERT (with an
explicit id) to keep those tests independent of create_clip's column coverage.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from tests.fixtures.factories import create_source, create_user


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


async def _seed_user(db) -> str:
    user_id = _uid("usr")
    await create_user(db, user_id=user_id, email=f"{user_id}@example.com")
    return user_id


async def _seed_source(db) -> str:
    source = await create_source(db, source_id=_uid("src"), title="Clip source")
    return source["id"]


async def _seed_task(db, user_id: str, source_id: str) -> str:
    return await TaskRepository.create_task(db, user_id=user_id, source_id=source_id, status="completed")


async def _cleanup(db, *, user_ids=(), source_ids=()):
    for source_id in source_ids:
        await db.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
    for user_id in user_ids:
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


async def _insert_clip(db, task_id: str, clip_order: int, **overrides) -> str:
    """Seed a generated_clips row directly; read/update/delete tests do not depend on create_clip."""
    clip_id = _uid("clip")
    await db.execute(
        text(
            """
            INSERT INTO generated_clips (
                id, task_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, clip_order,
                virality_score, hook_score, engagement_score, value_score, shareability_score,
                hook_type, hook_title, created_at, updated_at
            ) VALUES (
                :id, :task_id, :filename, :file_path, :start_time, :end_time, :duration,
                :text, :relevance_score, :reasoning, :clip_order,
                :virality_score, :hook_score, :engagement_score, :value_score, :shareability_score,
                :hook_type, :hook_title, NOW(), NOW()
            )
            """
        ),
        {
            "id": clip_id,
            "task_id": task_id,
            "filename": overrides.get("filename", f"clip-{clip_order}.mp4"),
            "file_path": overrides.get("file_path", f"/tmp/clip-{clip_order}.mp4"),
            "start_time": overrides.get("start_time", "00:00"),
            "end_time": overrides.get("end_time", "00:10"),
            "duration": overrides.get("duration", 10.0),
            "text": overrides.get("text", "Hook text"),
            "relevance_score": overrides.get("relevance_score", 0.9),
            "reasoning": overrides.get("reasoning", "Strong hook"),
            "clip_order": clip_order,
            "virality_score": overrides.get("virality_score", 0),
            "hook_score": overrides.get("hook_score", 0),
            "engagement_score": overrides.get("engagement_score", 0),
            "value_score": overrides.get("value_score", 0),
            "shareability_score": overrides.get("shareability_score", 0),
            "hook_type": overrides.get("hook_type"),
            "hook_title": overrides.get("hook_title"),
        },
    )
    return clip_id


# ---------------------------------------------------------------------------
# create_clip (A17 fix: explicit id on both INSERT paths -> round trip)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_create_clip_persists_and_returns_id(db_session, initialized_database):
    # Round-trip characterization of the A17 fix: create_clip now supplies an
    # explicit id on both INSERT paths, so the insert succeeds and the returned
    # id can be read back. Because create_clip commits internally (matching
    # TaskRepository.create_task), the row must also be visible to a NEW session
    # without any manual commit.
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        clip_id = await ClipRepository.create_clip(
            db_session,
            task_id=task_id,
            filename="clip-1.mp4",
            file_path="/tmp/clip-1.mp4",
            start_time="00:00",
            end_time="00:10",
            duration=10.0,
            text="Hook text",
            relevance_score=0.95,
            reasoning="Strong hook",
            clip_order=3,
            virality_score=8,
            hook_score=7,
            engagement_score=6,
            value_score=5,
            shareability_score=4,
            hook_type="question",
            hook_title="Best hook",
        )
        # create_clip returns the generated uuid as a string.
        assert isinstance(clip_id, str)
        assert str(UUID(clip_id)) == clip_id

        # Full-payload round trip within the creating session.
        clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
        assert clip["id"] == clip_id
        assert clip["task_id"] == task_id
        assert clip["filename"] == "clip-1.mp4"
        assert clip["file_path"] == "/tmp/clip-1.mp4"
        assert clip["start_time"] == "00:00"
        assert clip["end_time"] == "00:10"
        assert clip["duration"] == 10.0
        assert clip["text"] == "Hook text"
        assert clip["relevance_score"] == 0.95
        assert clip["reasoning"] == "Strong hook"
        assert clip["clip_order"] == 3
        assert clip["virality_score"] == 8
        assert clip["hook_score"] == 7
        assert clip["engagement_score"] == 6
        assert clip["value_score"] == 5
        assert clip["shareability_score"] == 4
        assert clip["hook_type"] == "question"
        assert clip["hook_title"] == "Best hook"

        # Durability: no manual commit here; create_clip must have committed
        # internally for the row to be visible to a fresh session.
        maker = async_sessionmaker(
            initialized_database, class_=AsyncSession, expire_on_commit=False
        )
        async with maker() as fresh_session:
            fresh_clip = await ClipRepository.get_clip_by_id(fresh_session, clip_id)
            assert fresh_clip is not None, (
                "create_clip row is not visible to a new session: create_clip "
                "did not commit internally"
            )
            assert fresh_clip["id"] == clip_id
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# get_clip_by_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_clip_by_id_returns_full_row_for_seeded_clip(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        clip_id = await _insert_clip(
            db_session,
            task_id,
            clip_order=3,
            filename="clip-1.mp4",
            file_path="/tmp/clip-1.mp4",
            start_time="00:00",
            end_time="00:10",
            duration=10.0,
            text="Hook text",
            relevance_score=0.95,
            reasoning="Strong hook",
            virality_score=8,
            hook_score=7,
            engagement_score=6,
            value_score=5,
            shareability_score=4,
            hook_type="question",
            hook_title="Best hook",
        )
        clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
        assert clip["id"] == clip_id
        assert clip["task_id"] == task_id
        assert clip["filename"] == "clip-1.mp4"
        assert clip["file_path"] == "/tmp/clip-1.mp4"
        assert clip["start_time"] == "00:00"
        assert clip["end_time"] == "00:10"
        assert clip["duration"] == 10.0
        assert clip["text"] == "Hook text"
        assert clip["relevance_score"] == 0.95
        assert clip["reasoning"] == "Strong hook"
        assert clip["clip_order"] == 3
        assert clip["virality_score"] == 8
        assert clip["hook_score"] == 7
        assert clip["engagement_score"] == 6
        assert clip["value_score"] == 5
        assert clip["shareability_score"] == 4
        assert clip["hook_type"] == "question"
        assert clip["hook_title"] == "Best hook"
        assert clip["video_url"] == f"/tasks/{task_id}/clips/{clip_id}/file"
        assert isinstance(clip["created_at"], str)  # isoformat string
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_clip_by_id_returns_none_for_unknown_clip(db_session):
    assert await ClipRepository.get_clip_by_id(db_session, _uid("clip")) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_clip_by_id_normalizes_null_scores_to_zero(db_session):
    # Read-path behavior: get_clip_by_id maps NULL score columns to 0.
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        clip_id = await _insert_clip(
            db_session, task_id, clip_order=1,
            virality_score=None, hook_score=None, engagement_score=None,
            value_score=None, shareability_score=None,
        )
        clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
        assert clip["virality_score"] == 0
        assert clip["hook_score"] == 0
        assert clip["engagement_score"] == 0
        assert clip["value_score"] == 0
        assert clip["shareability_score"] == 0
        assert clip["hook_type"] is None
        assert clip["hook_title"] is None
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# get_clips_by_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_clips_by_task_orders_by_clip_order(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        c_first = await _insert_clip(db_session, task_id, clip_order=2)
        c_second = await _insert_clip(db_session, task_id, clip_order=1)
        c_third = await _insert_clip(db_session, task_id, clip_order=3)

        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        assert [c["id"] for c in clips] == [c_second, c_first, c_third]
        assert [c["clip_order"] for c in clips] == [1, 2, 3]
        assert clips[0]["video_url"] == f"/tasks/{task_id}/clips/{c_second}/file"
        assert isinstance(clips[0]["created_at"], str)
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_clips_by_task_returns_empty_for_task_without_clips(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        assert await ClipRepository.get_clips_by_task(db_session, task_id) == []
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# get_clips_count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_clips_count_counts_only_task_clips(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_a = await _seed_task(db_session, user_id, source_id)
    task_b = await _seed_task(db_session, user_id, source_id)
    try:
        await _insert_clip(db_session, task_a, clip_order=1)
        await _insert_clip(db_session, task_a, clip_order=2)
        await _insert_clip(db_session, task_b, clip_order=1)

        assert await ClipRepository.get_clips_count(db_session, task_a) == 2
        assert await ClipRepository.get_clips_count(db_session, task_b) == 1
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# delete_clip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_delete_clip_removes_single_clip(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        victim = await _insert_clip(db_session, task_id, clip_order=1)
        survivor = await _insert_clip(db_session, task_id, clip_order=2)

        await ClipRepository.delete_clip(db_session, victim)

        assert await ClipRepository.get_clip_by_id(db_session, victim) is None
        assert await ClipRepository.get_clip_by_id(db_session, survivor) is not None
        assert await ClipRepository.get_clips_count(db_session, task_id) == 1
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# delete_clips_by_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_delete_clips_by_task_returns_count_and_empties_task(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    other_task = await _seed_task(db_session, user_id, source_id)
    try:
        for order in (1, 2, 3):
            await _insert_clip(db_session, task_id, clip_order=order)
        await _insert_clip(db_session, other_task, clip_order=1)

        deleted = await ClipRepository.delete_clips_by_task(db_session, task_id)

        assert deleted == 3
        assert await ClipRepository.get_clips_count(db_session, task_id) == 0
        assert await ClipRepository.get_clips_count(db_session, other_task) == 1
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# update_clip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_update_clip_persists_core_metadata(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        clip_id = await _insert_clip(db_session, task_id, clip_order=1)
        await ClipRepository.update_clip(
            db_session,
            clip_id,
            filename="renamed.mp4",
            file_path="/tmp/renamed.mp4",
            start_time="00:01",
            end_time="00:11",
            duration=11.0,
            text="Edited text",
        )
        clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
        assert clip["filename"] == "renamed.mp4"
        assert clip["file_path"] == "/tmp/renamed.mp4"
        assert clip["start_time"] == "00:01"
        assert clip["end_time"] == "00:11"
        assert clip["duration"] == 11.0
        assert clip["text"] == "Edited text"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# reorder_task_clips
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_reorder_task_clips_normalizes_sequence_to_1_to_n(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id)
    try:
        c_mid = await _insert_clip(db_session, task_id, clip_order=5)
        c_top = await _insert_clip(db_session, task_id, clip_order=2)
        c_last = await _insert_clip(db_session, task_id, clip_order=9)

        await ClipRepository.reorder_task_clips(db_session, task_id)

        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        # Order is by original clip_order (2, 5, 9), renumbered 1..n.
        assert [c["id"] for c in clips] == [c_top, c_mid, c_last]
        assert [c["clip_order"] for c in clips] == [1, 2, 3]
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])
