"""Shim for ai canonical at src.ai. Lazy."""
import warnings
warnings.warn("src.domain/caption/ai is shim for src.ai", DeprecationWarning, stacklevel=2)
import importlib as _il
_canon = None
def _get_canon():
    global _canon
    if _canon is None:
        _canon = _il.import_module("src.ai")
    return _canon
def __getattr__(name):
    return getattr(_get_canon(), name)
def __dir__():
    return dir(_get_canon())
