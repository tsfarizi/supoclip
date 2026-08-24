"""Shim re-export: canonical location is src.infra.db.repositories.api_key_repository. Do not add logic here."""
import warnings
warnings.warn("src.repositories/api_key_repository.py is deprecated, use src.infra.db.repositories.api_key_repository", DeprecationWarning, stacklevel=2)
from src.infra.db.repositories.api_key_repository import *  # noqa: F401,F403
import src.infra.db.repositories.api_key_repository as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
