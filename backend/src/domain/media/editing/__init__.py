"""Shim for clip_editor canonical at src.clip_editor. Lazy."""
import warnings
warnings.warn("src.domain/media/editing is shim for src.clip_editor", DeprecationWarning, stacklevel=2)
import importlib as _il
_canon = None
def _get_canon():
    global _canon
    if _canon is None:
        _canon = _il.import_module("src.clip_editor")
    return _canon
def __getattr__(name):
    return getattr(_get_canon(), name)
def __dir__():
    return dir(_get_canon())
