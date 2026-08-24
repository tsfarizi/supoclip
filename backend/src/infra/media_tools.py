"""Locate ffmpeg/ffprobe for the current process regardless of launcher.

run.ps1 prepends its portable ffmpeg dir to PATH before starting the API and
worker. Any other launch path (manual uvicorn/arq, IDE runner, service wrapper)
inherits a bare PATH where ``ffmpeg`` is not resolvable, and every subprocess
spawn then fails with WinError 2. This module probes the documented install
locations once per process and prepends them to ``os.environ["PATH"]``, so all
existing call sites (which exec "ffmpeg"/"ffprobe" by name) keep working.
"""

import logging
import os
from pathlib import Path
import shutil
from typing import List

logger = logging.getLogger(__name__)

_MEDIA_TOOL_DIRS_ANNOUNCED = False


def _candidate_dirs() -> List[Path]:
    """Directories that plausibly contain ffmpeg/ffprobe binaries."""
    candidates: List[Path] = []
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        # run.ps1's documented portable layout: %LOCALAPPDATA%\Programs\ffmpeg\<build>\bin
        base = Path(local_appdata) / "Programs" / "ffmpeg"
        if base.is_dir():
            for build in sorted(base.iterdir()):
                candidates.append(build / "bin")
            candidates.append(base / "bin")
    # Common static-install roots.
    candidates.append(Path("C:/ffmpeg/bin"))
    program_files = os.getenv("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "ffmpeg" / "bin")
    return [c for c in candidates if c.is_dir()]


def _has_tool(dir_path: Path, tool: str) -> bool:
    return (dir_path / f"{tool}.exe").is_file() or (dir_path / tool).is_file()


def ensure_media_tools_on_path() -> bool:
    """Make 'ffmpeg'/'ffprobe' resolvable for this process; idempotent.

    Returns True when both tools resolve afterwards. Safe to call from every
    entry point; the PATH mutation happens at most once.
    """
    global _MEDIA_TOOL_DIRS_ANNOUNCED
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        if not _MEDIA_TOOL_DIRS_ANNOUNCED:
            logger.info("ffmpeg/ffprobe resolved from existing PATH")
            _MEDIA_TOOL_DIRS_ANNOUNCED = True
        return True

    added: List[str] = []
    for dir_path in _candidate_dirs():
        if not (_has_tool(dir_path, "ffmpeg") or _has_tool(dir_path, "ffprobe")):
            continue
        text_dir = str(dir_path)
        if text_dir not in added and text_dir not in os.environ.get("PATH", "").split(os.pathsep):
            added.append(text_dir)

    if added:
        os.environ["PATH"] = (
            os.pathsep.join(added + [os.environ.get("PATH", "")])
        )
        logger.info("Added media tool dirs to PATH: %s", added)

    ok = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    if not ok and not _MEDIA_TOOL_DIRS_ANNOUNCED:
        logger.warning(
            "ffmpeg/ffprobe not found on PATH or known install locations; "
            "video processing will fail until ffmpeg is installed"
        )
    _MEDIA_TOOL_DIRS_ANNOUNCED = True
    return ok
