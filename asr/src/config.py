"""Service configuration read from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


class AsrConfig:
    """Runtime configuration for the ASR service, sourced from env vars at construction."""

    def __init__(self) -> None:
        self.asr_model = os.getenv("ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
        self.asr_aligner = os.getenv("ASR_ALIGNER", "Qwen/Qwen3-ForcedAligner-0.6B")
        self.port = int(os.getenv("ASR_PORT", "8765"))
        # 75-min podcasts need ~1100s; allow 60 min headroom for queue + aligner.
        self.request_timeout_seconds = int(os.getenv("ASR_REQUEST_TIMEOUT_SECONDS", "3600"))
        self.max_audio_mb = int(os.getenv("MAX_AUDIO_MB", "64"))
        self.fake_model = _parse_bool(os.getenv("ASR_FAKE_MODEL", "0"))
        self.model_cache_dir = os.getenv("HF_HOME", "/models")


@lru_cache(maxsize=1)
def get_config() -> AsrConfig:
    """Process-wide config singleton; built lazily on first call."""
    return AsrConfig()
