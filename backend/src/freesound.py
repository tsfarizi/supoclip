"""Shim re-export: canonical location is src.domain.ingestion.sfx.freesound. Do not add logic here."""
import warnings
warnings.warn("src.freesound.py is deprecated, use src.domain.ingestion.sfx.freesound", DeprecationWarning, stacklevel=2)
from src.domain.ingestion.sfx.freesound import *  # noqa: F401,F403
import src.domain.ingestion.sfx.freesound as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
