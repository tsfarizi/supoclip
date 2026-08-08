"""HTTP entry point for the SupoClip ASR service.

Contract: POST /v1/transcribe (multipart "audio") -> TranscribeResponse,
GET /health, GET /. Model lifecycle lives in ModelRuntime; this module only
orchestrates the HTTP layer, the serial inference queue, and error mapping.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.config import get_config
from src.errors import (
    AUDIO_INVALID,
    AUDIO_TOO_LARGE,
    INFERENCE_FAILED,
    MODEL_NOT_READY,
    TIMEOUT,
    VALIDATION,
    AsrError,
    build_error_response,
)
from src.model import ModelRuntime
from src.schemas import ErrorResponse, HealthResponse, TranscribeResponse

TRACE_HEADER = "x-trace-id"
APP_NAME = "SupoClip ASR Service"
APP_VERSION = "1.0.0"

logger = logging.getLogger(__name__)


class _SerialQueue:
    """Serializes inference: at most one transcription runs at a time.

    queue_depth = waiting requests + in-flight request.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._waiters = 0
        self._inflight = 0

    @property
    def depth(self) -> int:
        return self._waiters + self._inflight

    @asynccontextmanager
    async def acquire(self):
        self._waiters += 1
        try:
            await self._lock.acquire()
        finally:
            self._waiters -= 1
        self._inflight += 1
        try:
            yield
        finally:
            self._inflight -= 1
            self._lock.release()


def _trace_id(request: Request) -> str:
    # Middleware always sets request.state.trace_id; the fallback covers
    # exceptions raised before middleware ran (ServerErrorMiddleware layer).
    trace_id = getattr(request.state, "trace_id", None)
    return trace_id if isinstance(trace_id, str) else uuid.uuid4().hex[:12]


def create_app(model_runtime: ModelRuntime | None = None) -> FastAPI:
    config = get_config()
    runtime = model_runtime or ModelRuntime(config)
    queue = _SerialQueue()

    def _ensure_runtime() -> None:
        # Lifespan owns model loading in production (fail-fast at boot). This
        # fallback covers hosts that never run lifespan, e.g. TestClient used
        # without a context manager; in fake mode the load is instant.
        if not runtime.is_ready:
            runtime.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        try:
            runtime.load()
            logger.info("ASR model loaded")
        except Exception:
            logger.exception("ASR model load failed — failing fast")
            raise
        yield

    app = FastAPI(
        title=APP_NAME,
        description="Transcription service wrapping Qwen3-ASR behind an HTTP contract.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def trace_and_request_logging_middleware(request: Request, call_next):
        trace_id = request.headers.get(TRACE_HEADER) or uuid.uuid4().hex[:12]
        request.state.trace_id = trace_id
        started_at = time.perf_counter()

        logger.info("Incoming request %s %s", request.method, request.url.path)

        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception while processing request")
            raise

        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers[TRACE_HEADER] = trace_id
        logger.info(
            "Completed request %s %s with status %s in %sms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @app.exception_handler(AsrError)
    async def asr_error_handler(request: Request, exc: AsrError) -> JSONResponse:
        trace_id = _trace_id(request)
        logger.warning(
            "ASR error: %s status=%s detail=%s",
            exc.error_code,
            exc.status_code,
            exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_response(exc, trace_id).model_dump(),
            headers={TRACE_HEADER: trace_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        trace_id = _trace_id(request)
        err = AsrError(VALIDATION, f"Request validation failed: {exc}")
        logger.warning("Validation error: %s", exc.errors())
        return JSONResponse(
            status_code=err.status_code,
            content=build_error_response(err, trace_id).model_dump(),
            headers={TRACE_HEADER: trace_id},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        trace_id = _trace_id(request)
        logger.warning("HTTP exception: status=%s detail=%s", exc.status_code, exc.detail)
        body = ErrorResponse(
            detail=str(exc.detail),
            error_code="VALIDATION" if exc.status_code < 500 else "INFERENCE_FAILED",
            retryable=exc.status_code >= 500,
            trace_id=trace_id,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(),
            headers={TRACE_HEADER: trace_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = _trace_id(request)
        logger.exception("Unhandled server error on %s %s", request.method, request.url.path)
        err = AsrError(INFERENCE_FAILED, "An internal server error occurred")
        return JSONResponse(
            status_code=err.status_code,
            content=build_error_response(err, trace_id).model_dump(),
            headers={TRACE_HEADER: trace_id},
        )

    @app.post("/v1/transcribe", response_model=TranscribeResponse)
    async def transcribe(
        audio: UploadFile = File(...),
        language: str | None = Form(None),
        max_new_tokens: int = Form(256),
    ) -> dict:
        _ensure_runtime()
        if not runtime.is_ready:
            raise AsrError(MODEL_NOT_READY, "ASR model is not ready yet")

        max_new_tokens = max(1, min(max_new_tokens, 2048))
        data = await audio.read()
        if not data:
            raise AsrError(AUDIO_INVALID, "Uploaded audio file is empty")
        if len(data) > config.max_audio_mb * 1024 * 1024:
            raise AsrError(AUDIO_TOO_LARGE, f"Audio exceeds {config.max_audio_mb} MB limit")

        suffix = Path(audio.filename or "").suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(data)
            tmp_path = tmp.name
        finally:
            tmp.close()

        started_at = time.perf_counter()
        try:
            async with queue.acquire():
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(runtime.transcribe, tmp_path, language, max_new_tokens),
                        timeout=config.request_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    raise AsrError(
                        TIMEOUT,
                        f"Transcription timed out after {config.request_timeout_seconds}s",
                    ) from None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                # Windows may keep the file locked while a timed-out thread finishes.
                pass

        result["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
        return result

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> dict:
        st = runtime.status()
        return {
            "status": "ok" if runtime.is_ready else "loading",
            "model_ready": runtime.is_ready,
            "gpu": st["gpu"],
            "vram_free_mb": st["vram_free_mb"],
            "queue_depth": queue.depth,
        }

    @app.get("/")
    async def root() -> dict:
        return {"name": APP_NAME, "version": APP_VERSION, "docs": "/docs", "api": "/v1/transcribe"}

    app.state.runtime = runtime
    app.state.config = config
    return app


app = create_app()
