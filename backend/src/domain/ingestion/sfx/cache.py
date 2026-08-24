"""Validated MP3 storage and immutable sound-effect attribution manifests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ....shared.config import get_config
from .freesound import FreesoundSound

logger = logging.getLogger(__name__)

PROVIDER = "freesound"
TTL_SECONDS = 30 * 24 * 60 * 60
MAX_CACHE_BYTES = 1024**3
MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_TASK_BYTES = 100 * 1024 * 1024
MANIFEST_VERSION = 1
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SoundEffectAttribution:
    provider: str
    sound_id: str
    title: str
    creator: str
    source_url: str
    license: str
    source_type: str
    local_mp3_path: str


@dataclass(frozen=True)
class CachedSoundEffect:
    path: Path
    checksum: str
    variant: str
    attribution: SoundEffectAttribution
    size_bytes: int


class SoundEffectCacheError(RuntimeError):
    """Raised when a sound asset or manifest violates its storage contract."""


def cache_dir() -> Path:
    configured = os.getenv("SOUND_EFFECT_CACHE_DIR")
    root = Path(configured) if configured else Path(get_config().temp_dir) / "sound_effect_cache"
    root.mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(exist_ok=True)
    (root / "manifests").mkdir(exist_ok=True)
    return root


def _component(value: str, field: str) -> str:
    value = str(value).strip()
    if not value or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audio_is_readable(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            header = source.read(4096)
        has_mp3_header = header.startswith(b"ID3") or any(
            header[index] == 0xFF and (header[index + 1] & 0xE0) == 0xE0
            for index in range(max(0, len(header) - 1))
        )
        if not has_mp3_header:
            return False
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "mp3"
    except (OSError, subprocess.SubprocessError):
        return False


def _asset_path(provider: str, sound_id: str, variant: str, checksum: str) -> Path:
    return cache_dir() / "assets" / f"{_component(provider, 'provider')}__{_component(sound_id, 'sound_id')}__{_component(variant, 'variant')}__{checksum}.mp3"


def _sidecar_path(asset: Path) -> Path:
    return asset.with_suffix(".json")


def _manifest_path(task_id: str) -> Path:
    return cache_dir() / "manifests" / f"{_component(task_id, 'task_id')}.json"


def _read_sidecar(asset: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(_sidecar_path(asset).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _attribution_from_payload(payload: dict[str, object]) -> SoundEffectAttribution:
    fields = ("provider", "sound_id", "title", "creator", "source_url", "license", "source_type", "local_mp3_path")
    values = {field: payload.get(field) for field in fields}
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        raise SoundEffectCacheError("sound attribution metadata is incomplete")
    return SoundEffectAttribution(**values)  # type: ignore[arg-type]


def _validated_entry(asset: Path, expected_checksum: str | None = None) -> CachedSoundEffect | None:
    if not asset.is_file() or asset.stat().st_size > MAX_ASSET_BYTES:
        return None
    payload = _read_sidecar(asset)
    if payload is None:
        return None
    checksum = payload.get("checksum")
    fetched_at = payload.get("fetched_at")
    if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum) or not isinstance(fetched_at, (int, float)):
        return None
    if expected_checksum and checksum != expected_checksum:
        return None
    if time.time() - fetched_at > TTL_SECONDS or _checksum(asset) != checksum or not _audio_is_readable(asset):
        return None
    try:
        attribution = _attribution_from_payload(payload)
        variant = _component(str(payload["variant"]), "variant")
    except (KeyError, TypeError, ValueError, SoundEffectCacheError):
        return None
    return CachedSoundEffect(asset, checksum, variant, attribution, asset.stat().st_size)


def get_cached_sound_effect(provider: str, sound_id: str | int, variant: str = "preview-hq-mp3", checksum: str | None = None) -> CachedSoundEffect | None:
    """Return a validated cached asset; return None for a miss or corruption."""
    provider = _component(provider, "provider")
    sound_id = _component(str(sound_id), "sound_id")
    variant = _component(variant, "variant")
    if checksum is not None and not _SHA256.fullmatch(checksum):
        raise ValueError("checksum must be a lowercase SHA-256 digest")
    candidates = cache_dir().joinpath("assets").glob(f"{provider}__{sound_id}__{variant}__*.mp3")
    for asset in candidates:
        entry = _validated_entry(asset, checksum)
        if entry is not None:
            _touch_last_access(asset)
            return entry
    return None


def cache_sound_effect(sound: FreesoundSound, source_path: Path, task_id: str, variant: str = "preview-hq-mp3", source_type: str = "freesound") -> CachedSoundEffect:
    """Validate and atomically cache one Freesound MP3, enforcing asset/task limits."""
    if not isinstance(sound, FreesoundSound):
        raise TypeError("sound must be FreesoundSound")
    if not source_path.is_file() or source_path.stat().st_size == 0 or source_path.stat().st_size > MAX_ASSET_BYTES:
        raise SoundEffectCacheError("sound asset is missing or exceeds 25MB")
    if not _audio_is_readable(source_path):
        raise SoundEffectCacheError("sound asset is not a readable MP3")
    variant = _component(variant, "variant")
    task_id = _component(task_id, "task_id")
    source_type = _component(source_type, "source_type")
    checksum = _checksum(source_path)
    destination = _asset_path(PROVIDER, str(sound.id), variant, checksum)
    attribution = SoundEffectAttribution(PROVIDER, str(sound.id), sound.name, sound.username, sound.url, sound.license, source_type, str(destination))
    payload = {**attribution.__dict__, "checksum": checksum, "variant": variant, "fetched_at": time.time(), "last_access": time.time()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        descriptor, temporary_name = tempfile.mkstemp(suffix=".mp3", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source_path, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    sidecar_temporary = _sidecar_path(destination).with_suffix(".json.tmp")
    sidecar_temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(sidecar_temporary, _sidecar_path(destination))
    entry = _validated_entry(destination, checksum)
    if entry is None:
        raise SoundEffectCacheError("cached sound asset failed validation")
    existing = read_attribution_manifest(task_id) if _manifest_path(task_id).exists() else ()
    try:
        write_attribution_manifest(task_id, (*existing, entry.attribution))
    except Exception:
        logger.warning("Sound-effect attribution manifest degraded", exc_info=True)
    cleanup_sound_effect_cache()
    return entry


def write_attribution_manifest(task_id: str, assets: Sequence[SoundEffectAttribution]) -> Path:
    """Atomically write a task manifest, rejecting missing assets and over-budget tasks."""
    path = _manifest_path(task_id)
    unique = {asset.local_mp3_path: asset for asset in assets}
    total = 0
    for asset in unique.values():
        candidate = Path(asset.local_mp3_path)
        try:
            candidate.resolve().relative_to((cache_dir() / "assets").resolve())
        except ValueError as error:
            raise SoundEffectCacheError("manifest asset is outside the sound cache") from error
        entry = _validated_entry(candidate)
        if entry is None or entry.attribution != asset:
            raise SoundEffectCacheError("manifest references an invalid or mismatched asset")
        total += entry.size_bytes
    if total > MAX_TASK_BYTES:
        raise SoundEffectCacheError("task sound assets exceed 100MB")
    payload = {"version": MANIFEST_VERSION, "task_id": task_id, "assets": [asset.__dict__ for asset in unique.values()]}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_attribution_manifest(task_id: str) -> tuple[SoundEffectAttribution, ...]:
    """Read and validate a task attribution manifest."""
    payload = json.loads(_manifest_path(task_id).read_text(encoding="utf-8"))
    if payload.get("version") != MANIFEST_VERSION or not isinstance(payload.get("assets"), list):
        raise SoundEffectCacheError("invalid attribution manifest")
    return tuple(_attribution_from_payload(item) for item in payload["assets"] if isinstance(item, dict))


def cleanup_sound_effect_cache(now: float | None = None) -> int:
    """Remove expired, corrupt, orphaned, then least-recently-used assets."""
    now = time.time() if now is None else now
    root = cache_dir()
    referenced = {str(Path(asset.local_mp3_path).resolve()) for manifest in (root / "manifests").glob("*.json") for asset in _manifest_assets(manifest)}
    removed = 0
    entries: list[tuple[Path, float, int]] = []
    for asset in (root / "assets").glob("*.mp3"):
        payload = _read_sidecar(asset)
        expired = payload is None or not isinstance(payload.get("fetched_at"), (int, float)) or now - float(payload["fetched_at"]) > TTL_SECONDS
        orphan = str(asset.resolve()) not in referenced
        if expired or orphan:
            _remove_asset(asset)
            removed += 1
            continue
        entries.append((asset, float(payload.get("last_access", asset.stat().st_mtime)), asset.stat().st_size))
    total = sum(size for _, _, size in entries)
    for asset, _access, size in sorted(entries, key=lambda item: item[1]):
        if total <= MAX_CACHE_BYTES:
            break
        _remove_asset(asset)
        total -= size
        removed += 1
    return removed


def _manifest_assets(path: Path) -> list[SoundEffectAttribution]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [_attribution_from_payload(item) for item in payload.get("assets", []) if isinstance(item, dict)]
    except (OSError, ValueError, SoundEffectCacheError, AttributeError):
        return []


def _remove_asset(asset: Path) -> None:
    asset.unlink(missing_ok=True)
    _sidecar_path(asset).unlink(missing_ok=True)


def _touch_last_access(asset: Path) -> None:
    try:
        sidecar = _sidecar_path(asset)
        payload = _read_sidecar(asset)
        if payload is None:
            return
        temporary = sidecar.with_suffix(".json.tmp")
        payload["last_access"] = time.time()
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, sidecar)
    except OSError:
        logger.warning("Unable to update sound-effect cache access time", exc_info=True)
