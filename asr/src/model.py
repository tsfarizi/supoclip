"""ModelRuntime: owns the Qwen3-ASR model lifecycle behind a lazy-import boundary.

torch and qwen_asr are imported only inside methods so that this module (and
anything importing it, including the FastAPI app) loads on machines without GPU
or without the gpu extra installed.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import AsrConfig
from src.errors import AsrError, INFERENCE_FAILED, MODEL_NOT_READY

logger = logging.getLogger(__name__)


def _to_ms(value: float) -> int:
    # Qwen forced aligner returns seconds; this service's contract is
    # milliseconds — if the upstream unit ever changes, fix here only.
    return int(round(float(value) * 1000))


def _normalize_stamp_items(stamps: Any) -> list[Any]:
    # qwen-asr 0.0.6 returns time_stamps as a flat list of ForcedAlignItem
    # (older/other versions may nest per chunk); accept both shapes.
    items: list[Any] = []
    for chunk in stamps:
        if hasattr(chunk, "text"):
            items.append(chunk)
        elif isinstance(chunk, (list, tuple)):
            items.extend(chunk)
    return items


class ModelRuntime:
    def __init__(self, config: AsrConfig) -> None:
        self._config = config
        self._model: Any = None
        self._fake = config.fake_model
        self._gpu_name: str | None = None
        self._vram_free_mb: int | None = None
        self._loaded = False

    def load(self) -> None:
        if self._fake:
            logger.info("FAKE model mode — deterministic responses, no GPU/torch required")
            self._loaded = True
            return

        import torch
        from qwen_asr import Qwen3ASRModel

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available — ASR service requires a GPU (fail-fast)")

        dtype = torch.bfloat16
        self._model = Qwen3ASRModel.from_pretrained(
            self._config.asr_model,
            dtype=dtype,
            device_map="cuda:0",
            max_inference_batch_size=1,
            max_new_tokens=256,
            forced_aligner=self._config.asr_aligner,
            forced_aligner_kwargs=dict(dtype=dtype, device_map="cuda:0"),
        )
        self._gpu_name = torch.cuda.get_device_name(0)
        free, _ = torch.cuda.mem_get_info(0)
        self._vram_free_mb = int(free // (1024 * 1024))
        self._loaded = True
        logger.info("ASR model ready on %s (vram free %s MB)", self._gpu_name, self._vram_free_mb)

    @property
    def is_ready(self) -> bool:
        return self._loaded

    def status(self) -> dict[str, Any]:
        return {"gpu": self._gpu_name, "vram_free_mb": self._vram_free_mb}

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        max_new_tokens: int = 256,
    ) -> dict[str, Any]:
        if self._fake:
            return self._fake_transcribe()
        if self._model is None:
            raise AsrError(MODEL_NOT_READY, "ASR model is not loaded")

        try:
            results = self._model.transcribe(
                audio=audio_path,
                language=language or None,
                return_time_stamps=True,
            )
            r = results[0]
            text = r.text
            lang = getattr(r, "language", None) or "unknown"
            stamps = getattr(r, "time_stamps", None)
            words: list[dict[str, Any]] = []
            timestamps = False
            if stamps:
                items = _normalize_stamp_items(stamps)
                words = [
                    {"text": it.text, "start_ms": _to_ms(it.start_time), "end_ms": _to_ms(it.end_time)}
                    for it in items
                ]
                timestamps = bool(items)
        except Exception as exc:
            import torch

            if isinstance(exc, torch.cuda.OutOfMemoryError):
                raise AsrError(
                    INFERENCE_FAILED,
                    "GPU out of memory during inference — reduce ASR_MODEL to "
                    "Qwen/Qwen3-ASR-0.6B or move aligner to CPU",
                ) from exc
            raise AsrError(INFERENCE_FAILED, f"Inference failed: {exc}", retryable=True) from exc

        return {
            "language": lang,
            "text": text,
            "words": words,
            "timestamps": timestamps,
            "duration_ms": words[-1]["end_ms"] if words else 0,
            "elapsed_ms": 0,
            "model": self._config.asr_model,
            "aligner": self._config.asr_aligner,
        }

    def _fake_transcribe(self) -> dict[str, Any]:
        words = [
            {"text": "Hello", "start_ms": 0, "end_ms": 300},
            {"text": "world", "start_ms": 300, "end_ms": 600},
            {"text": "from", "start_ms": 600, "end_ms": 900},
            {"text": "fake", "start_ms": 900, "end_ms": 1200},
            {"text": "ASR.", "start_ms": 1200, "end_ms": 1500},
        ]
        return {
            "language": "en",
            "text": "Hello world from fake ASR.",
            "words": words,
            "timestamps": True,
            "duration_ms": 1500,
            "elapsed_ms": 0,
            "model": self._config.asr_model,
            "aligner": self._config.asr_aligner,
        }

    def unload(self) -> None:
        self._model = None
        self._loaded = False
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
