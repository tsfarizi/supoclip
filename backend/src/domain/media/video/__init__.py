"""Shim for video_utils canonical at src.video_utils. Lazy to avoid circular import with transitions."""
import warnings
warnings.warn("src.domain.media.video is shim for src.video_utils", DeprecationWarning, stacklevel=2)
import importlib as _il
_canon = None
def _get_canon():
    global _canon
    if _canon is None:
        _canon = _il.import_module("src.video_utils")
    return _canon
def __getattr__(name):
    return getattr(_get_canon(), name)
def __dir__():
    return dir(_get_canon())
