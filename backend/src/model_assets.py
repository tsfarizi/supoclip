"""Shim re-export: canonical location is src.infra.assets.model_assets. Do not add logic here."""
import warnings
warnings.warn("src.model_assets.py is deprecated, use src.infra.assets.model_assets", DeprecationWarning, stacklevel=2)
from src.infra.assets.model_assets import *  # noqa: F401,F403
import src.infra.assets.model_assets as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
