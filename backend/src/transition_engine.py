"""Shim re-export: canonical location is src.domain.media.video.transitions.engine. Do not add logic here."""
import warnings
warnings.warn("src.transition_engine.py is deprecated, use src.domain.media.video.transitions.engine", DeprecationWarning, stacklevel=2)
from src.domain.media.video.transitions.engine import *  # noqa: F401,F403
import src.domain.media.video.transitions.engine as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
