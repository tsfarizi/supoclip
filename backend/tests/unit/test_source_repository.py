"""
Characterization tests for SourceRepository.

Freeze the CURRENT database behavior of SourceRepository before any refactor.
These rows back the LEFT JOIN used by TaskRepository.get_task_by_id and
get_user_tasks, so freezing their shape matters for the task refactor.
Each test uses unique ids and cleans up after itself.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.repositories.source_repository import SourceRepository


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


async def _cleanup(db, *, source_ids=()):
    for source_id in source_ids:
        await db.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
    await db.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_create_source_round_trip_persists_all_fields(db_session):
    source_id = await SourceRepository.create_source(
        db_session, "youtube", "Roundtrip title", "https://example.com/v.mp4"
    )
    try:
        source = await SourceRepository.get_source_by_id(db_session, source_id)
        assert source["id"] == source_id
        assert source["type"] == "youtube"
        assert source["title"] == "Roundtrip title"
        assert source["url"] == "https://example.com/v.mp4"
        assert source["created_at"] is not None
    finally:
        await _cleanup(db_session, source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_create_source_without_url_fails_loud(db_session):
    # B3 fallback removed: sources.url is NOT NULL (Schema v2), so omitting the
    # URL must raise instead of silently writing a broken row.
    with pytest.raises(IntegrityError):
        await SourceRepository.create_source(db_session, "video_url", "No URL")
    await db_session.rollback()  # clear the aborted transaction


@pytest.mark.asyncio(loop_scope="session")
async def test_get_source_by_id_returns_none_for_unknown_source(db_session):
    assert await SourceRepository.get_source_by_id(db_session, _uid("src")) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_update_source_title_persists(db_session):
    source_id = await SourceRepository.create_source(
        db_session, "youtube", "Original title", "https://example.com/v.mp4"
    )
    try:
        await SourceRepository.update_source_title(db_session, source_id, "Renamed title")
        source = await SourceRepository.get_source_by_id(db_session, source_id)
        assert source["title"] == "Renamed title"
        assert source["url"] == "https://example.com/v.mp4"  # untouched
    finally:
        await _cleanup(db_session, source_ids=[source_id])
