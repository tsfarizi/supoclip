"""Shim for models canonical at src.models. Lazy."""
import warnings
warnings.warn("src.infra/db/models is shim for src.models", DeprecationWarning, stacklevel=2)
import importlib as _il
_canon = None
def _get_canon():
    global _canon
    if _canon is None:
        _canon = _il.import_module("src.models")
    return _canon
def __getattr__(name):
    return getattr(_get_canon(), name)
def __dir__():
    return dir(_get_canon())
