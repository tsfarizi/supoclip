"""Shim re-export: canonical location is src.shared.observability. Do not add logic here."""
import warnings
warnings.warn("src.observability.py is deprecated, use src.shared.observability", DeprecationWarning, stacklevel=2)
from src.shared.observability import *  # noqa: F401,F403
import src.shared.observability as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
