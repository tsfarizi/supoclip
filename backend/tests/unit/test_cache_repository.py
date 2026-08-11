"""
Characterization tests for CacheRepository.

Freeze the CURRENT database behavior of CacheRepository before any refactor.
Each test uses a unique cache_key and deletes its rows at the end.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.repositories.cache_repository import CacheRepository


def _cache_key() -> str:
    return f"char-{uuid4().hex}"


async def _cleanup(db, *, cache_keys=()):
    for key in cache_keys:
        await db.execute(
            text("DELETE FROM processing_cache WHERE cache_key = :key"), {"key": key}
        )
    await db.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_get_cache_returns_none_when_key_absent(db_session):
    assert await CacheRepository.get_cache(db_session, _cache_key()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_cache_inserts_then_get_returns_full_row(db_session):
    key = _cache_key()
    await CacheRepository.upsert_cache(
        db_session,
        cache_key=key,
        source_url="https://example.com/video.mp4",
        source_type="video_url",
        video_path="/tmp/video.mp4",
        transcript_text="Full transcript",
        analysis_json='{"topics": ["a"]}',
    )
    try:
        row = await CacheRepository.get_cache(db_session, key)
        assert row["cache_key"] == key
        assert row["source_url"] == "https://example.com/video.mp4"
        assert row["source_type"] == "video_url"
        assert row["video_path"] == "/tmp/video.mp4"
        assert row["transcript_text"] == "Full transcript"
        assert row["analysis_json"] == '{"topics": ["a"]}'
    finally:
        await _cleanup(db_session, cache_keys=[key])


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_cache_second_call_updates_provided_fields_and_keeps_nulls(db_session):
    key = _cache_key()
    await CacheRepository.upsert_cache(
        db_session,
        cache_key=key,
        source_url="https://example.com/video.mp4",
        source_type="video_url",
        video_path="/tmp/first.mp4",
        transcript_text="First transcript",
        analysis_json=None,
    )
    # Second upsert: provide a new video_path and analysis_json, but leave
    # transcript_text unset. COALESCE(EXCLUDED.x, processing_cache.x) means
    # NULL fields on the second call must keep their previous values.
    await CacheRepository.upsert_cache(
        db_session,
        cache_key=key,
        source_url="https://example.com/other.mp4",
        source_type="youtube",
        video_path="/tmp/second.mp4",
        transcript_text=None,
        analysis_json='{"x": 1}',
    )
    try:
        row = await CacheRepository.get_cache(db_session, key)
        assert row["source_url"] == "https://example.com/other.mp4"
        assert row["source_type"] == "youtube"
        assert row["video_path"] == "/tmp/second.mp4"
        assert row["transcript_text"] == "First transcript"  # preserved
        assert row["analysis_json"] == '{"x": 1}'
    finally:
        await _cleanup(db_session, cache_keys=[key])
