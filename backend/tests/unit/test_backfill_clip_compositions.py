import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.migrations.backfill_clip_compositions import backfill_clip_compositions
from tests.fixtures.factories import create_source, create_user
from src.repositories.task_repository import TaskRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_backfill_clip_compositions(db_session):
    user_id = "usr-backfill-test"
    await create_user(db_session, user_id=user_id, email=f"{user_id}@example.com")
    source = await create_source(db_session, source_id="src-backfill-test", title="Clip source")
    source_id = source["id"]
    task_id = await TaskRepository.create_task(db_session, user_id=user_id, source_id=source_id, status="completed")

    clip_id = "clip-backfill-1"
    await db_session.execute(
        text("""
            INSERT INTO generated_clips (
                id, task_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, clip_order, composition_json, created_at, updated_at
            ) VALUES (
                :id, :task_id, 'clip.mp4', '/tmp/clip.mp4', '00:10', '00:20', 10.0,
                'Clip text', 0.8, 'Good clip', 1, NULL, NOW(), NOW()
            )
            ON CONFLICT (id) DO UPDATE SET composition_json = NULL
        """),
        {"id": clip_id, "task_id": task_id},
    )
    await db_session.commit()

    try:
        count = await backfill_clip_compositions(db_session)
        assert count >= 1

        res = await db_session.execute(
            text("SELECT composition_json, composition_version FROM generated_clips WHERE id = :id"),
            {"id": clip_id},
        )
        row = res.fetchone()
        assert row is not None
        assert row.composition_json is not None
        assert "schema_version" in row.composition_json
        assert "source_asset_ref" in row.composition_json
        assert row.composition_version == 1
    finally:
        await db_session.execute(text("DELETE FROM generated_clips WHERE id = :id"), {"id": clip_id})
        await db_session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task_id})
        await db_session.execute(text("DELETE FROM sources WHERE id = :id"), {"id": source_id})
        await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await db_session.commit()


