"""Error taxonomy and response builder for the ASR HTTP contract."""

from __future__ import annotations

from src.schemas import ErrorResponse


class _ErrorKind:
    """Static metadata for one error code of the taxonomy."""

    __slots__ = ("error_code", "status_code", "retryable")

    def __init__(self, error_code: str, status_code: int, retryable: bool) -> None:
        self.error_code = error_code
        self.status_code = status_code
        self.retryable = retryable


AUDIO_INVALID = _ErrorKind("AUDIO_INVALID", 400, False)
AUDIO_TOO_LARGE = _ErrorKind("AUDIO_TOO_LARGE", 413, False)
VALIDATION = _ErrorKind("VALIDATION", 422, False)
MODEL_NOT_READY = _ErrorKind("MODEL_NOT_READY", 503, True)
TIMEOUT = _ErrorKind("TIMEOUT", 504, True)
INFERENCE_FAILED = _ErrorKind("INFERENCE_FAILED", 500, True)


class AsrError(Exception):
    """Domain error carrying the HTTP status, taxonomy code, and retryability."""

    def __init__(self, kind: _ErrorKind, detail: str, *, retryable: bool | None = None) -> None:
        super().__init__(detail)
        self.error_code = kind.error_code
        self.status_code = kind.status_code
        self.retryable = kind.retryable if retryable is None else retryable
        self.detail = detail


def build_error_response(err: AsrError, trace_id: str | None = None) -> ErrorResponse:
    return ErrorResponse(
        detail=err.detail,
        error_code=err.error_code,
        retryable=err.retryable,
        trace_id=trace_id,
    )
