"""Falsification tests for the persistent YouTube video cache (src/video_cache.py).

Covers the U14 contract: lookup validates file + sidecar + TTL, store is
atomic and writes the sidecar, invalidate removes both artifacts, eviction is
LRU against VIDEO_CACHE_MAX_GB, and every failure degrades to None/source_path
instead of raising. All ffprobe probing is monkeypatched so the tests run
without a real video or external process.
"""

import json
import time
from pathlib import Path

import pytest

from src import video_cache
from src.config import Config, set_config_override


@pytest.fixture
def cache_config(tmp_path, monkeypatch):
    config = Config()
    config.video_cache_enabled = True
    config.video_cache_dir = str(tmp_path / "cache")
    config.video_cache_ttl_hours = 24
    config.video_cache_max_gb = 0
    monkeypatch.setattr(video_cache, "_probe_duration", lambda _path: 30.0)
    monkeypatch.setattr(video_cache, "_probe_size", lambda _path: (1280, 720))
    set_config_override(config)
    yield config
    set_config_override(None)


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "src.mp4"
    path.write_bytes(b"x" * 4096)
    return path


def test_store_then_lookup_roundtrip(cache_config, source_file):
    stored = video_cache.store(source_file, "abcdefghijk", duration_seconds=30.0)
    assert stored.exists()

    entry = video_cache.lookup("abcdefghijk")
    assert entry is not None
    assert entry.path == stored
    assert entry.duration_seconds == 30.0
    assert entry.height == 720


def test_lookup_miss_when_no_entry(cache_config):
    assert video_cache.lookup("missingvideo") is None


def test_lookup_invalidates_corrupt_file(cache_config, source_file):
    stored = video_cache.store(source_file, "abcdefghijk")
    assert stored.exists()
    stored.write_bytes(b"tiny")  # below MIN_VALID_SIZE_BYTES

    assert video_cache.lookup("abcdefghijk") is None
    assert not stored.exists()  # invalidated


def test_lookup_invalidates_missing_sidecar(cache_config, source_file):
    stored = video_cache.store(source_file, "abcdefghijk")
    stored.with_suffix(".json").unlink()

    assert video_cache.lookup("abcdefghijk") is None
    assert not stored.exists()


def test_lookup_invalidates_unprobeable_file(cache_config, source_file, monkeypatch):
    stored = video_cache.store(source_file, "abcdefghijk")
    assert stored.exists()
    # Sidecar without duration + failing probe -> invalid.
    monkeypatch.setattr(video_cache, "_probe_duration", lambda _path: None)
    sidecar = stored.with_suffix(".json")
    payload = json.loads(sidecar.read_text())
    payload.pop("duration_seconds", None)
    sidecar.write_text(json.dumps(payload))

    assert video_cache.lookup("abcdefghijk") is None
    assert not stored.exists()


def test_lookup_expired_by_ttl(cache_config, source_file):
    cache_config.video_cache_ttl_hours = 24
    stored = video_cache.store(source_file, "abcdefghijk")
    assert stored.exists()

    # Rewind the sidecar's fetched_at beyond the TTL.
    sidecar = stored.with_suffix(".json")
    payload = json.loads(sidecar.read_text())
    payload["fetched_at"] = time.time() - 25 * 3600
    sidecar.write_text(json.dumps(payload))

    assert video_cache.lookup("abcdefghijk") is None
    assert not stored.exists()  # expired -> invalidated


def test_zero_ttl_never_expires(cache_config, source_file):
    cache_config.video_cache_ttl_hours = 0
    stored = video_cache.store(source_file, "abcdefghijk")
    sidecar = stored.with_suffix(".json")
    payload = json.loads(sidecar.read_text())
    payload["fetched_at"] = time.time() - 10 * 24 * 3600  # 10 days ago
    sidecar.write_text(json.dumps(payload))

    entry = video_cache.lookup("abcdefghijk")
    assert entry is not None


def test_store_returns_source_when_cache_disabled(source_file):
    config = Config()
    config.video_cache_enabled = False
    set_config_override(config)
    try:
        result = video_cache.store(source_file, "abcdefghijk")
        assert result == source_file
    finally:
        set_config_override(None)


def test_lookup_none_when_cache_disabled(source_file):
    config = Config()
    config.video_cache_enabled = False
    set_config_override(config)
    try:
        assert video_cache.lookup("abcdefghijk") is None
    finally:
        set_config_override(None)


def test_store_atomic_no_partial_read(cache_config, source_file):
    stored = video_cache.store(source_file, "abcdefghijk")
    # No .tmp sibling left behind.
    assert not Path(str(stored) + ".tmp").exists()
    assert not Path(str(stored.with_suffix(".json")) + ".tmp").exists()


def test_evict_lru_until_under_limit(cache_config, source_file, tmp_path, monkeypatch):
    cache_config.video_cache_max_gb = 0  # disable for seeding
    for video_id in ("aaaa", "bbbb", "cccc"):
        video_cache.store(source_file, video_id)

    # Pretend each entry is 4GiB so three entries exceed the 10GiB cap.
    four_gib = 4 * 1024 ** 3
    monkeypatch.setattr(
        video_cache,
        "_read_entries",
        lambda: [
            ("aaaa", 0.0, four_gib),
            ("bbbb", 1.0, four_gib),
            ("cccc", 2.0, four_gib),
        ],
    )
    cache_config.video_cache_max_gb = 10

    removed = video_cache.evict_if_needed()
    assert removed >= 1
    assert not video_cache._video_path("aaaa").exists()  # LRU evicted first
    remaining = [vid for vid in ("aaaa", "bbbb", "cccc") if video_cache._video_path(vid).exists()]
    assert len(remaining) <= 2
