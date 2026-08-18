"""Typed failure taxonomy for the task processing pipeline.

Raise these at the point of failure; TaskService.process_task maps the type
to the persisted error_code instead of string-matching the message text.
All subclasses inherit the base __init__, so str(error) is the message
passed at raise time.
"""


class TaskProcessingError(Exception):
    """Base class for all task pipeline processing failures."""

    def __init__(self, message: str):
        super().__init__(message)


class DownloadError(TaskProcessingError):
    """Video acquisition (download or source file resolution) failed."""


class TranscriptionError(TaskProcessingError):
    """Transcript generation failed."""


class AnalysisError(TaskProcessingError):
    """AI transcript analysis failed."""


class RenderError(TaskProcessingError):
    """Clip rendering failed."""


class CancelledError(TaskProcessingError):
    """Task was cancelled by the user."""


class InvalidSourceError(ValueError):
    """Source URL is not a supported video link (neither YouTube nor upload://).

    Raised at task creation time from client input validation, so it subclasses
    ValueError (idiomatic bad-input signal) instead of the processing pipeline
    taxonomy: it never fires inside process_task.
    """


class DuplicateTaskError(Exception):
    """A task for the same source identity is already in flight.

    Raised when the unique partial index on tasks.source_identity rejects a
    concurrent duplicate submission. This is the DB-backed guarantee that
    replaces the best-effort Python pre-check: the constraint decides, so two
    parallel submissions of the same video resolve to exactly one winner.
    """
