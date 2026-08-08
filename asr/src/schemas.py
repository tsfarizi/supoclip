"""HTTP contract schemas for the ASR service."""

from __future__ import annotations

from pydantic import BaseModel


class Word(BaseModel):
    text: str
    start_ms: int
    end_ms: int


class TranscribeResponse(BaseModel):
    language: str
    text: str
    words: list[Word]
    timestamps: bool
    duration_ms: int
    elapsed_ms: int
    model: str
    aligner: str


class HealthResponse(BaseModel):
    status: str
    model_ready: bool
    gpu: str | None = None
    vram_free_mb: int | None = None
    queue_depth: int = 0


class ErrorResponse(BaseModel):
    detail: str
    error_code: str
    retryable: bool
    trace_id: str | None = None
