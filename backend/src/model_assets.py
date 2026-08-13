"""
YuNet face-detection model asset management (download from Hugging Face).

The detector chain in video_utils prefers YuNet (OpenCV DNN) for accuracy.
This module resolves and downloads the model file via Python over Hugging Face
using the public resolve URL (no API key required), caches it under
``{temp_dir}/models``, and validates it with a real ``FaceDetectorYN.create``
probe before trusting it. Every failure degrades to ``None`` so the caller's
detector chain falls back to MediaPipe/Haar — the model is an accelerator, not
a gate.

Candidate sources are tried in order: the official OpenCV repo first (FP32
2023mar, the best-accuracy release in the official set), then the latest
dynamic-input 2026may redistribution whose weights are identical. The exact
source can be pinned via YUNET_MODEL_REPO / YUNET_MODEL_FILE.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
import time

from .config import get_config

logger = logging.getLogger(__name__)

MODELS_SUBDIR = "models"
MIN_MODEL_SIZE_BYTES = 100_000
DOWNLOAD_TIMEOUT = (5, 20)
# Module-level memo so a process only attempts resolution once; the value is
# (path_or_None, mono_time) and negative results are retried after an hour.
_RESOLVED: Optional[Tuple[Optional[Path], float]] = None
NEGATIVE_RESOLVE_TTL_SECONDS = 3600

# (repo_id, filename) candidates, best-accuracy official source first.
YUNET_CANDIDATES: List[Tuple[str, str]] = [
    (
        "opencv/face_detection_yunet",
        "face_detection_yunet_2023mar.onnx",
    ),
    (
        "pollen-robotics/face_detection_yunet_2026may",
        "face_detection_yunet_2026may.onnx",
    ),
]


def model_cache_dir() -> Path:
    """Directory where downloaded models are stored (created lazily)."""
    config = get_config()
    root = Path(config.temp_dir) / MODELS_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _candidate_sources() -> List[Tuple[str, str]]:
    """Resolve configured overrides against the default candidate list."""
    config = get_config()
    overrides: List[Tuple[str, str]] = []
    if config.yunet_model_repo and config.yunet_model_file:
        overrides.append((config.yunet_model_repo, config.yunet_model_file))
    elif config.yunet_model_repo:
        # Repo pinned but no file: reuse the first candidate filename.
        overrides.append((config.yunet_model_repo, YUNET_CANDIDATES[0][1]))
    elif config.yunet_model_file:
        overrides.append((YUNET_CANDIDATES[0][0], config.yunet_model_file))
    return overrides or YUNET_CANDIDATES


def _resolve_url(repo_id: str, filename: str, revision: str = "main") -> str:
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"


def _probe_model(path: Path) -> bool:
    """Return True when cv2.FaceDetectorYN can actually load the model."""
    if path.stat().st_size < MIN_MODEL_SIZE_BYTES:
        return False
    try:
        import cv2

        detector = cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.5, 0.3, 5000)
        detector.setInputSize((320, 320))
        return detector is not None
    except Exception as exc:
        logger.warning("YuNet model probe failed for %s: %s", path.name, exc)
        return False


def _download(url: str, dest: Path) -> bool:
    """Download to a temp sibling and atomically replace dest. Never raises."""
    import requests

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
        if tmp.stat().st_size < MIN_MODEL_SIZE_BYTES:
            tmp.unlink(missing_ok=True)
            logger.warning("YuNet download too small from %s", url)
            return False
        tmp.replace(dest)
        return True
    except Exception as exc:
        logger.warning("YuNet download failed from %s: %s", url, exc)
        tmp.unlink(missing_ok=True)
        return False


def resolve_yunet_model() -> Optional[Path]:
    """Return a validated YuNet model path, downloading it if necessary.

    Order: explicit local path (YUNET_MODEL_PATH) -> cached file -> download
    candidates -> None. The result is memoized per process; a failed resolve is
    retried only after NEGATIVE_RESOLVE_TTL_SECONDS so offline workers do not
    hammer Hugging Face on every clip.
    """
    global _RESOLVED
    now = time.monotonic()
    if _RESOLVED is not None:
        path, resolved_at = _RESOLVED
        if path is not None or now - resolved_at < NEGATIVE_RESOLVE_TTL_SECONDS:
            return path

    config = get_config()
    if config.yunet_model_path:
        explicit = Path(config.yunet_model_path)
        if explicit.exists() and _probe_model(explicit):
            logger.info("Using configured YuNet model: %s", explicit)
            _RESOLVED = (explicit, now)
            return explicit
        logger.warning(
            "Configured YUNET_MODEL_PATH %s unusable; trying downloads", explicit
        )

    if not config.yunet_auto_download:
        _RESOLVED = (None, now)
        return None

    cache = model_cache_dir()
    result: Optional[Path] = None
    for repo_id, filename in _candidate_sources():
        dest = cache / filename
        if dest.exists() and _probe_model(dest):
            result = dest
            break
        logger.info("Downloading YuNet model %s/%s from Hugging Face", repo_id, filename)
        if _download(_resolve_url(repo_id, filename), dest) and _probe_model(dest):
            result = dest
            break

    _RESOLVED = (result, now)
    if result is None:
        logger.warning(
            "YuNet model unavailable; face detector chain falls back to MediaPipe/Haar"
        )
    return result


def reset_resolve_cache_for_tests() -> None:
    """Clear the per-process memo (test isolation only)."""
    global _RESOLVED
    _RESOLVED = None
