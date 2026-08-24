"""Shim re-export: canonical location is src.worker. Do not add logic here."""
import warnings
warnings.warn("src.worker_main is deprecated, use src.worker", DeprecationWarning, stacklevel=2)
from src.worker import *  # noqa: F401,F403
import src.worker as _canon
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
