import tempfile
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.domain.media.composition import SourceAssetRef
from src.domain.media.resolver import SourceAssetResolver, SourceUnavailableError


@pytest.mark.asyncio
async def test_resolver_raises_for_none_ref(db_session):
    with pytest.raises(SourceUnavailableError):
        await SourceAssetResolver.resolve(db_session, None)


@pytest.mark.asyncio
async def test_resolver_resolves_upload_url(db_session):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    upload_url = f"upload://{tmp_path.name}"
    with patch("src.domain.media.resolver._resolve_upload_path", return_value=tmp_path):
        ref = SourceAssetRef(source_url=upload_url)
        resolved = await SourceAssetResolver.resolve(db_session, ref)
        assert resolved == tmp_path.resolve()

    if tmp_path.exists():
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_resolver_resolves_from_processing_cache(db_session):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("src.domain.media.resolver.CacheRepository.get_cache", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"video_path": str(tmp_path)}
        ref = SourceAssetRef(source_identity="youtube:abc12345")
        resolved = await SourceAssetResolver.resolve(db_session, ref)
        assert resolved == tmp_path.resolve()

    if tmp_path.exists():
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_resolver_resolves_from_source_table(db_session):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("src.domain.media.resolver.SourceRepository.get_source_by_id", new_callable=AsyncMock) as mock_src, \
         patch("src.domain.media.resolver.CacheRepository.get_cache", new_callable=AsyncMock) as mock_cache:
        mock_src.return_value = {"url": "https://youtube.com/watch?v=mockvid"}
        mock_cache.return_value = {"video_path": str(tmp_path)}

        ref = SourceAssetRef(source_id="src-999")
        resolved = await SourceAssetResolver.resolve(db_session, ref)
        assert resolved == tmp_path.resolve()

    if tmp_path.exists():
        tmp_path.unlink()


@pytest.mark.asyncio
async def test_resolver_raises_when_file_not_on_disk(db_session):
    with patch("src.domain.media.resolver.CacheRepository.get_cache", new_callable=AsyncMock) as mock_cache, \
         patch("src.domain.media.resolver.SourceRepository.get_source_by_id", new_callable=AsyncMock) as mock_src:
        mock_cache.return_value = {"video_path": "/non/existent/file.mp4"}
        mock_src.return_value = None

        ref = SourceAssetRef(source_identity="youtube:missing123")
        with pytest.raises(SourceUnavailableError, match="Source video is no longer available on disk"):
            await SourceAssetResolver.resolve(db_session, ref)


@pytest.mark.asyncio
async def test_resolver_resolves_from_youtube_cache_dir(db_session, tmp_path):
    video_file = tmp_path / "vid123.mp4"
    video_file.write_bytes(b"dummy")

    with patch("src.domain.media.resolver.CacheRepository.get_cache", new_callable=AsyncMock) as mock_cache, \
         patch("src.config.get_config") as mock_cfg:
        mock_cache.return_value = None
        cfg_mock = MagicMock()
        cfg_mock.video_cache_dir = str(tmp_path)
        mock_cfg.return_value = cfg_mock

        ref = SourceAssetRef(source_identity="youtube:vid123")
        resolved = await SourceAssetResolver.resolve(db_session, ref)
        assert resolved == video_file.resolve()

