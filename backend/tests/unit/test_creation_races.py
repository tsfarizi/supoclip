"""T5 (P4): duplicate-submission race falsification at the storage layer.

Contracts under test (src.services.task_service.TaskService):
  1. Two PARALLEL ``create_task_with_source`` calls (asyncio.gather, two
     independent sessions) for the same YouTube URL resolve to exactly one
     success and one ``DuplicateTaskError``. The partial unique index
     ``uq_tasks_source_identity_active`` decides at INSERT/commit time; the
     loser's transaction rolls back and nothing leaks.
  2. The ``find_active_task_for_source`` pre-check still detects the winning
     in-flight task afterwards (the Python pre-check remains functional even
     though the storage constraint is authoritative).
  3. Resume of a legacy task whose source_url/source_type are NULL (simulating
     a pre-backfill row) returns HTTP 400 at the resume route.

Isolation: every test seeds its own uuid user/task and removes them in
teardown, so no row collides with other units (no fixed 'user-1'/'task-1').
All tests use the session loop because the conftest session-scoped database
fixture is loop-bound.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Config
from src.errors import DuplicateTaskError
from src.services.task_service import TaskService
from tests.fixtures.factories import create_user


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


def _unique_youtube_url() -> str:
    # get_youtube_video_id validates the ID length is exactly 11 chars.
    return f"https://www.youtube.com/watch?v={uuid4().hex[:11]}"


def _signed_headers(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(
        b"test-backend-auth-secret", payload, hashlib.sha256
    ).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


async def _cleanup_user(db, *, user_id: str, source_ids: tuple[str, ...] = ()) -> None:
    await db.execute(text("DELETE FROM tasks WHERE user_id = :uid"), {"uid": user_id})
    for source_id in source_ids:
        await db.execute(
            text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id}
        )
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# 1. Parallel duplicate creation -> exactly one winner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_parallel_duplicate_creation_resolves_to_one_winner(initialized_database):
    session_maker = async_sessionmaker(
        initialized_database, class_=AsyncSession, expire_on_commit=False
    )
    user_id = _uid("usr")
    source_id = None
    try:
        async with session_maker() as seed_session:
            await create_user(seed_session, user_id=user_id)

        video_url = _unique_youtube_url()

        async def _attempt(session: AsyncSession):
            service = TaskService(session)
            # title passed explicitly so no YouTube metadata network call runs.
            return await service.create_task_with_source(
                user_id=user_id, url=video_url, title="Race video"
            )

        session_a = session_maker()
        session_b = session_maker()
        try:
            results = await asyncio.gather(
                _attempt(session_a), _attempt(session_b), return_exceptions=True
            )
        finally:
            await session_a.close()
            await session_b.close()

        successes = [r for r in results if isinstance(r, str)]
        duplicates = [r for r in results if isinstance(r, DuplicateTaskError)]
        others = [
            r for r in results
            if not isinstance(r, str) and not isinstance(r, DuplicateTaskError)
        ]
        assert not others, f"unexpected outcome from the race: {others}"
        assert len(successes) == 1, f"expected exactly one winner, got {successes}"
        assert len(duplicates) == 1, f"expected exactly one DuplicateTaskError"

        # The loser's transaction must not leave a source row behind.
        winner_task_id = successes[0]
        async with session_maker() as check_session:
            task = await TaskService(check_session).task_repo.get_task_by_id(
                check_session, winner_task_id
            )
            assert task is not None
            assert task["source_identity"] is not None
            source_id = task["source_id"]
            source_count = (
                await check_session.execute(
                    text("SELECT count(*) FROM sources WHERE id = :sid"),
                    {"sid": task["source_id"]},
                )
            ).scalar()
            assert source_count == 1
            task_count = (
                await check_session.execute(
                    text("SELECT count(*) FROM tasks WHERE source_identity = :ident"),
                    {"ident": task["source_identity"]},
                )
            ).scalar()
            assert task_count == 1
    finally:
        async with session_maker() as cleanup_session:
            await _cleanup_user(
                cleanup_session, user_id=user_id,
                source_ids=(source_id,) if source_id else (),
            )


# ---------------------------------------------------------------------------
# 2. find_active_task_for_source still detects the in-flight winner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_find_active_task_for_source_detects_winning_task(initialized_database):
    session_maker = async_sessionmaker(
        initialized_database, class_=AsyncSession, expire_on_commit=False
    )
    user_id = _uid("usr")
    source_id = None
    try:
        async with session_maker() as seed_session:
            await create_user(seed_session, user_id=user_id)

        video_url = _unique_youtube_url()
        async with session_maker() as create_session:
            task_id = await TaskService(create_session).create_task_with_source(
                user_id=user_id, url=video_url, title="Active video"
            )
        async with session_maker() as check_session:
            task = await TaskService(check_session).task_repo.get_task_by_id(
                check_session, task_id
            )
            source_id = task["source_id"]

            # The Python pre-check is not dead: an in-flight task with the same
            # canonical identity is still visible to the old helper.
            found = await TaskService(check_session).find_active_task_for_source(
                video_url
            )
            assert found is not None
            assert found["id"] == task_id
            # Different YouTube URL -> not detected.
            assert (
                await TaskService(check_session).find_active_task_for_source(
                    _unique_youtube_url()
                )
                is None
            )
    finally:
        async with session_maker() as cleanup_session:
            await _cleanup_user(
                cleanup_session, user_id=user_id,
                source_ids=(source_id,) if source_id else (),
            )


# ---------------------------------------------------------------------------
# 3. Resume of a legacy task with NULL source columns -> HTTP 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_resume_legacy_task_with_null_source_columns_returns_400(
    client, db_session
):
    """P4: resume reads columns only; a task whose source_url/source_type are
    NULL (no source row, i.e. legacy pre-backfill) must fail with 400 instead
    of silently resuming with defaults."""
    user_id = _uid("usr")
    task_id = _uid("task")
    await create_user(db_session, user_id=user_id)
    # A legacy row: no source row (source_id NULL -> LEFT JOIN yields NULL
    # source_url/source_type). Render-settings columns are populated so the
    # 400 is specifically about the missing source columns.
    await db_session.execute(
        text(
            """
            INSERT INTO tasks (
                id, user_id, status, output_format, add_subtitles,
                sound_effects_count, processing_mode, cache_hit, share_enabled,
                created_at, updated_at
            ) VALUES (
                :id, :user_id, 'error', 'vertical', true,
                0, 'fast', false, false,
                NOW(), NOW()
            )
            """
        ),
        {"id": task_id, "user_id": user_id},
    )
    await db_session.commit()
    try:
        response = await client.post(
            f"/tasks/{task_id}/resume", headers=_signed_headers(user_id)
        )

        assert response.status_code == 400
        detail = response.json().get("detail", "")
        assert "Task source information is missing" in detail, detail
    finally:
        await db_session.execute(
            text("DELETE FROM tasks WHERE id = :tid"), {"tid": task_id}
        )
        await db_session.execute(
            text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
        )
        await db_session.commit()