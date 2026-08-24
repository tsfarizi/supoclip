"""Shim for youtube_utils canonical at src.youtube_utils. Lazy."""
import warnings
warnings.warn("src.domain/ingestion/youtube/service is shim for src.youtube_utils", DeprecationWarning, stacklevel=2)
import importlib as _il
_canon = None
def _get_canon():
    global _canon
    if _canon is None:
        _canon = _il.import_module("src.youtube_utils")
    return _canon
def __getattr__(name):
    return getattr(_get_canon(), name)
def __dir__():
    return dir(_get_canon())
