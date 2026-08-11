"""
Characterization tests for TaskRepository.

These tests freeze the CURRENT database behavior of TaskRepository before any
refactor. They call the static repository methods directly against the real
Postgres test database (see tests/conftest.py).

Isolation contract: every test seeds unique user/source/task rows and deletes
them at the end, so the suite is repeatable and independent of execution order.
Aggregation queries (get_active_tasks_with_sources, get_performance_metrics)
are asserted only against rows owned by the test's unique user/mode, so leftover
data from other tests or previous runs cannot flip them.

Loop scope: tests are marked `asyncio(loop_scope="session")` because conftest
configures `asyncio_default_fixture_loop_scope=session`. asyncpg connections are
bound to the loop that created them; running the test body in a function-scoped
loop while the session-scoped `db_session` fixture uses the session loop leaves
an open transaction whose teardown rollback crashes with a cross-loop asyncpg
error. Running the test in the session loop keeps one consistent loop.

B3 note: create_task/get_task_by_id/update_task_settings previously contained a
legacy fallback path (INSERT/SELECT/UPDATE without caption_template,
include_broll, processing_mode). The fallback is removed; the single fail-loud
query is what these tests exercise against the current schema.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.repositories.task_repository import TaskRepository
from tests.fixtures.factories import create_source, create_user


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


async def _seed_user(db, email: str | None = None) -> str:
    user_id = _uid("usr")
    await create_user(db, user_id=user_id, email=email or f"{user_id}@example.com")
    return user_id


async def _seed_source(db, title: str = "Characterization source", url: str | None = "https://www.youtube.com/watch?v=seeded") -> str:
    source = await create_source(db, source_id=_uid("src"), title=title, url=url)
    return source["id"]


async def _cleanup(db, *, user_ids=(), source_ids=(), cache_keys=()):
    for key in cache_keys:
        await db.execute(
            text("DELETE FROM processing_cache WHERE cache_key = :key"), {"key": key}
        )
    for source_id in source_ids:
        await db.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
    for user_id in user_ids:
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


async def _create_clip(db, task_id: str, clip_order: int) -> str:
    # Seed rows via direct SQL (explicit id) to keep this helper independent of
    # ClipRepository.create_clip's column coverage.
    clip_id = _uid("clip")
    await db.execute(
        text(
            """
            INSERT INTO generated_clips (
                id, task_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, clip_order, created_at, updated_at
            ) VALUES (
                :id, :task_id, :filename, :file_path, :start_time, :end_time, :duration,
                :text, :relevance_score, :reasoning, :clip_order, NOW(), NOW()
            )
            """
        ),
        {
            "id": clip_id,
            "task_id": task_id,
            "filename": f"clip-{clip_order}.mp4",
            "file_path": f"/tmp/clip-{clip_order}.mp4",
            "start_time": "00:00",
            "end_time": "00:10",
            "duration": 10.0,
            "text": "Hook",
            "relevance_score": 0.9,
            "reasoning": "Strong hook",
            "clip_order": clip_order,
        },
    )
    return clip_id


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_create_task_round_trip_persists_all_fields(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(
        db_session,
        user_id=user_id,
        source_id=source_id,
        status="processing",
        font_family="Arial",
        font_size=32,
        font_color="#ABCDEF",
    )
    try:
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["id"] == task_id
        assert task["user_id"] == user_id
        assert task["source_id"] == source_id
        assert task["status"] == "processing"
        assert task["font_family"] == "Arial"
        assert task["font_size"] == 32
        assert task["font_color"] == "#ABCDEF"
        # Signature defaults are what get persisted when not passed.
        assert task["caption_template"] == "default"
        assert task["include_broll"] is False
        assert task["processing_mode"] == "fast"
        # Newly created rows get the column server defaults.
        assert task["progress"] == 0
        assert task["generated_clips_ids"] is None
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_create_task_persists_caption_template_broll_and_processing_mode(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(
        db_session,
        user_id=user_id,
        source_id=source_id,
        caption_template="minimal",
        include_broll=True,
        processing_mode="quality",
    )
    try:
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["caption_template"] == "minimal"
        assert task["include_broll"] is True
        assert task["processing_mode"] == "quality"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# get_task_by_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_task_by_id_joins_source_title_type_and_url(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session, title="Join me")
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["source_title"] == "Join me"
        assert task["source_type"] == "youtube"
        assert task["source_url"] == "https://www.youtube.com/watch?v=seeded"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_task_by_id_returns_none_for_unknown_task(db_session):
    assert await TaskRepository.get_task_by_id(db_session, _uid("task")) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_task_by_id_left_joins_when_source_is_deleted(db_session):
    # ON DELETE SET NULL on tasks.source_id: deleting the source leaves a task
    # whose joined source fields must be None rather than the row disappearing.
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    await db_session.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
    await db_session.commit()
    try:
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["source_id"] is None
        assert task["source_title"] is None
        assert task["source_type"] is None
        assert task["source_url"] is None
    finally:
        await _cleanup(db_session, user_ids=[user_id])


# ---------------------------------------------------------------------------
# update_task_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_status_persists_status_progress_and_message(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    try:
        await TaskRepository.update_task_status(
            db_session, task_id, "completed", progress=87, progress_message="Rendering final"
        )
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "completed"
        assert task["progress"] == 87
        assert task["progress_message"] == "Rendering final"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_status_without_progress_keeps_previous_progress_and_message(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    try:
        await TaskRepository.update_task_status(
            db_session, task_id, "processing", progress=50, progress_message="Halfway"
        )
        await TaskRepository.update_task_status(db_session, task_id, "completed")
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "completed"
        # progress/progress_message are only written when not None.
        assert task["progress"] == 50
        assert task["progress_message"] == "Halfway"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_status_persists_explicit_zero_progress(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    try:
        await TaskRepository.update_task_status(db_session, task_id, "processing", progress=0)
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["progress"] == 0
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# update_task_runtime_metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_runtime_metadata_persists_cache_hit(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        await TaskRepository.update_task_runtime_metadata(db_session, task_id, cache_hit=True)
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["cache_hit"] is True
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_runtime_metadata_persists_error_timings_and_stage_data(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    started = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 8, 1, 10, 2, 0, tzinfo=timezone.utc)
    try:
        await TaskRepository.update_task_runtime_metadata(
            db_session,
            task_id,
            error_code="E42",
            stage_timings_json='{"download": 1.5}',
            started_at=started,
            completed_at=completed,
        )
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["error_code"] == "E42"
        assert task["stage_timings_json"] == '{"download": 1.5}'
        assert task["started_at"] == started
        assert task["completed_at"] == completed
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_runtime_metadata_with_all_none_is_noop(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        await TaskRepository.update_task_runtime_metadata(db_session, task_id)
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["cache_hit"] is False
        assert task["error_code"] is None
        assert task["started_at"] is None
        assert task["completed_at"] is None
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# update_task_settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_settings_persists_font_and_caption_options(db_session):
    # Single fail-loud UPDATE (B3 fallback removed) with the full column set,
    # including the Schema v2 render settings (output_format / add_subtitles /
    # cleanup_settings_json) that made the Redis task_source key only a cache.
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        await TaskRepository.update_task_settings(
            db_session,
            task_id,
            "Roboto",
            40,
            "#112233",
            "kicker",
            True,
            output_format="original",
            add_subtitles=False,
            cleanup_settings_json={
                "cut_long_pauses": True,
                "pause_threshold_ms": 1200,
                "remove_filler_words": True,
                "filtered_words": ["basically", "like"],
            },
        )
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["font_family"] == "Roboto"
        assert task["font_size"] == 40
        assert task["font_color"] == "#112233"
        assert task["caption_template"] == "kicker"
        assert task["include_broll"] is True
        assert task["output_format"] == "original"
        assert task["add_subtitles"] is False
        cleanup = task["cleanup_settings_json"]
        parsed_cleanup = json.loads(cleanup) if isinstance(cleanup, str) else cleanup
        assert parsed_cleanup == {
            "cut_long_pauses": True,
            "pause_threshold_ms": 1200,
            "remove_filler_words": True,
            "filtered_words": ["basically", "like"],
        }
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# update_task_clips
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_clips_persists_generated_clip_ids(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    clip_ids = [str(uuid4()) for _ in range(3)]
    try:
        await TaskRepository.update_task_clips(db_session, task_id, clip_ids)
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["generated_clips_ids"] == clip_ids
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# enable_sharing / disable_sharing / get_shared_task_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_enable_sharing_returns_token_and_resolves_shared_task(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="completed")
    token = _uid("tok")
    try:
        returned = await TaskRepository.enable_sharing(db_session, task_id, token)
        assert returned == token
        assert await TaskRepository.get_shared_task_id(db_session, token) == task_id
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_enable_sharing_twice_keeps_original_token(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="completed")
    token1 = _uid("tok")
    token2 = _uid("tok")
    try:
        first = await TaskRepository.enable_sharing(db_session, task_id, token1)
        second = await TaskRepository.enable_sharing(db_session, task_id, token2)
        assert first == token1
        assert second == token1  # second call must not overwrite a live token
        assert await TaskRepository.get_shared_task_id(db_session, token1) == task_id
        assert await TaskRepository.get_shared_task_id(db_session, token2) is None
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_disable_sharing_then_enable_rotates_token(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="completed")
    token1 = _uid("tok")
    token2 = _uid("tok")
    try:
        await TaskRepository.enable_sharing(db_session, task_id, token1)
        assert await TaskRepository.disable_sharing(db_session, task_id) is True
        assert await TaskRepository.get_shared_task_id(db_session, token1) is None

        await TaskRepository.enable_sharing(db_session, task_id, token2)
        # The revoked token stays dead; the new token is live.
        assert await TaskRepository.get_shared_task_id(db_session, token1) is None
        assert await TaskRepository.get_shared_task_id(db_session, token2) == task_id
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_shared_task_id_only_resolves_completed_tasks(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    token = _uid("tok")
    try:
        await TaskRepository.enable_sharing(db_session, task_id, token)
        # share_enabled is TRUE but status is not 'completed' -> unresolvable.
        assert await TaskRepository.get_shared_task_id(db_session, token) is None
        await TaskRepository.update_task_status(db_session, task_id, "completed")
        assert await TaskRepository.get_shared_task_id(db_session, token) == task_id
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_enable_sharing_returns_none_for_unknown_task(db_session):
    assert await TaskRepository.enable_sharing(db_session, _uid("task"), _uid("tok")) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_disable_sharing_returns_false_for_unknown_task(db_session):
    assert await TaskRepository.disable_sharing(db_session, _uid("task")) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_get_shared_task_id_returns_none_for_unknown_token(db_session):
    assert await TaskRepository.get_shared_task_id(db_session, _uid("tok")) is None


# ---------------------------------------------------------------------------
# user_exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_user_exists_true_for_existing_user(db_session):
    user_id = await _seed_user(db_session)
    try:
        assert await TaskRepository.user_exists(db_session, user_id) is True
    finally:
        await _cleanup(db_session, user_ids=[user_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_user_exists_false_for_unknown_user(db_session):
    assert await TaskRepository.user_exists(db_session, _uid("usr")) is False


# ---------------------------------------------------------------------------
# get_user_tasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_user_tasks_orders_desc_and_counts_clips(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session, title="First video")
    older = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="completed")
    await asyncio.sleep(0.02)
    newer = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    try:
        for _ in range(2):
            await _create_clip(db_session, older, clip_order=1)

        tasks = await TaskRepository.get_user_tasks(db_session, user_id)
        assert [t["id"] for t in tasks] == [newer, older]  # created_at DESC
        assert tasks[0]["clips_count"] == 0
        assert tasks[1]["clips_count"] == 2
        assert tasks[1]["source_title"] == "First video"
        assert tasks[1]["source_type"] == "youtube"
        assert tasks[1]["source_url"] == "https://www.youtube.com/watch?v=seeded"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_user_tasks_respects_limit(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    older = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    await asyncio.sleep(0.02)
    newer = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        tasks = await TaskRepository.get_user_tasks(db_session, user_id, limit=1)
        assert [t["id"] for t in tasks] == [newer]
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_user_tasks_excludes_other_users_tasks(db_session):
    owner = await _seed_user(db_session)
    other = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    try:
        await TaskRepository.create_task(db_session, user_id=owner, source_id=source_id)
        await TaskRepository.create_task(db_session, user_id=other, source_id=source_id)
        tasks = await TaskRepository.get_user_tasks(db_session, owner)
        assert len(tasks) == 1
        assert tasks[0]["user_id"] == owner
    finally:
        await _cleanup(db_session, user_ids=[owner, other], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_user_tasks_returns_empty_list_for_user_without_tasks(db_session):
    user_id = await _seed_user(db_session)
    try:
        assert await TaskRepository.get_user_tasks(db_session, user_id) == []
    finally:
        await _cleanup(db_session, user_ids=[user_id])


# ---------------------------------------------------------------------------
# get_active_tasks_with_sources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_tasks_with_sources_excludes_terminal_statuses(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session, title="Active source")
    active_processing = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    active_pending = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="pending")
    for terminal_status in ("completed", "error", "cancelled", "deleted"):
        await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status=terminal_status)
    try:
        rows = await TaskRepository.get_active_tasks_with_sources(db_session)
        mine = {r["id"]: r for r in rows if r["user_id"] == user_id}
        assert set(mine) == {active_processing, active_pending}
        assert mine[active_processing]["source_url"] == "https://www.youtube.com/watch?v=seeded"
        assert mine[active_pending]["source_url"] == "https://www.youtube.com/watch?v=seeded"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_tasks_with_sources_coalesces_missing_url_to_empty_string(db_session):
    # sources.url is NOT NULL (Schema v2), so a NULL joined url can no longer be
    # seeded directly. The COALESCE(s.url, '') contract is still reachable when
    # the source row is deleted (tasks.source_id -> ON DELETE SET NULL), which
    # yields a NULL s.url on the LEFT JOIN.
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="processing")
    await db_session.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
    await db_session.commit()
    try:
        rows = await TaskRepository.get_active_tasks_with_sources(db_session)
        mine = [r for r in rows if r["user_id"] == user_id]
        assert len(mine) == 1
        assert mine[0]["id"] == task_id
        assert mine[0]["source_url"] == ""  # COALESCE(s.url, '')
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# get_task_notification_context / mark_completion_notification_sent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_task_notification_context_returns_user_and_source_info(db_session):
    email = f"{_uid('mail')}@example.com"
    user_id = await _seed_user(db_session, email=email)
    source_id = await _seed_source(db_session, title="Notification video")
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        ctx = await TaskRepository.get_task_notification_context(db_session, task_id)
        assert ctx["task_id"] == task_id
        assert ctx["notify_on_completion"] is True  # users.notify_on_completion server default
        assert ctx["completion_notification_sent_at"] is None
        assert ctx["source_title"] == "Notification video"
        assert ctx["user_email"] == email
        assert ctx["user_name"] == "Test User"
        assert ctx["user_first_name"] is None
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_task_notification_context_returns_none_for_unknown_task(db_session):
    assert await TaskRepository.get_task_notification_context(db_session, _uid("task")) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_completion_notification_sent_is_idempotent(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id)
    try:
        first = await TaskRepository.mark_completion_notification_sent(db_session, task_id)
        assert first is True
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["completion_notification_sent_at"] is not None

        second = await TaskRepository.mark_completion_notification_sent(db_session, task_id)
        assert second is False  # idempotent: already marked
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# get_performance_metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_performance_metrics_groups_timed_tasks_by_processing_mode(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    mode = f"char-{uuid4().hex[:8]}"
    base = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    try:
        t1 = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, processing_mode=mode)
        await TaskRepository.update_task_runtime_metadata(
            db_session, t1, started_at=base, completed_at=base + timedelta(seconds=120)
        )
        t2 = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, processing_mode=mode)
        await TaskRepository.update_task_runtime_metadata(
            db_session, t2, started_at=base, completed_at=base + timedelta(seconds=60)
        )
        # Task with the same mode but no timestamps must be excluded.
        await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, processing_mode=mode)

        metrics = await TaskRepository.get_performance_metrics(db_session)
        entry = next((m for m in metrics["modes"] if m["processing_mode"] == mode), None)
        assert entry is not None
        assert entry["total_tasks"] == 2
        assert entry["avg_seconds"] == pytest.approx(90.0)
        assert entry["p50_seconds"] == pytest.approx(90.0)   # percentile_cont(0.5) of [60, 120]
        assert entry["p95_seconds"] == pytest.approx(117.0)  # percentile_cont(0.95) of [60, 120]
        assert entry["cache_hit_rate"] == 0
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_performance_metrics_counts_cache_hits_in_rate(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    mode = f"char-{uuid4().hex[:8]}"
    base = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    try:
        t1 = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, processing_mode=mode)
        await TaskRepository.update_task_runtime_metadata(
            db_session, t1, cache_hit=True, started_at=base, completed_at=base + timedelta(seconds=30)
        )
        t2 = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, processing_mode=mode)
        await TaskRepository.update_task_runtime_metadata(
            db_session, t2, cache_hit=False, started_at=base, completed_at=base + timedelta(seconds=60)
        )

        metrics = await TaskRepository.get_performance_metrics(db_session)
        entry = next((m for m in metrics["modes"] if m["processing_mode"] == mode), None)
        assert entry is not None
        assert entry["total_tasks"] == 2
        assert entry["cache_hit_rate"] == pytest.approx(0.5)
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_performance_metrics_has_no_entry_for_mode_without_timestamps(db_session):
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    mode = f"char-{uuid4().hex[:8]}"
    try:
        await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, processing_mode=mode)
        metrics = await TaskRepository.get_performance_metrics(db_session)
        assert all(m["processing_mode"] != mode for m in metrics["modes"])
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])
