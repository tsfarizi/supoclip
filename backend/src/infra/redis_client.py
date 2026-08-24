"""Shim re-export: canonical location is src.infra.cache.redis_client. Do not add logic here."""
import warnings
warnings.warn("src.infra/redis_client.py is deprecated, use src.infra.cache.redis_client", DeprecationWarning, stacklevel=2)
from src.infra.cache.redis_client import *  # noqa: F401,F403
import src.infra.cache.redis_client as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
