"""Shim re-export: canonical location is src.app. Do not add logic here."""
import warnings
warnings.warn("src.main_refactored is deprecated, use src.app", DeprecationWarning, stacklevel=2)
from src.app import *  # noqa: F401,F403
import src.app as _canon
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
