"""Shim re-export: canonical location is src.infra.db.repositories.source_repository. Do not add logic here."""
import warnings
warnings.warn("src.repositories/source_repository.py is deprecated, use src.infra.db.repositories.source_repository", DeprecationWarning, stacklevel=2)
from src.infra.db.repositories.source_repository import *  # noqa: F401,F403
import src.infra.db.repositories.source_repository as _canon
import sys as _sys
# Re-export public names and support __getattr__ for any future additions
globals().update({k: getattr(_canon, k) for k in dir(_canon) if not k.startswith("_")})
def __getattr__(name):
    return getattr(_canon, name)
def __dir__():
    return dir(_canon)
