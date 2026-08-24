"""Shim re-export: canonical location is src.domain.ingestion.sfx.cache. Do not add logic here."""
import warnings
warnings.warn("src.sound_effect_cache.py is deprecated, use src.domain.ingestion.sfx.cache", DeprecationWarning, stacklevel=2)
from src.domain.ingestion.sfx.cache import *  # noqa: F401,F403
import src.domain.ingestion.sfx.cache as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
