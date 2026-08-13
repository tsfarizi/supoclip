"""
Media API routes (fonts, transitions, uploads).
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Any, Optional, cast
import logging
import os
import re
import shutil
import subprocess
import uuid
import aiofiles

from ...admin_auth import require_admin_user
from ...config import get_config
from ...database import get_db
from ...auth_headers import resolve_authenticated_user_id
from ...services.billing_service import BillingService
from ...font_registry import (
    FONTS_DIR,
    SUPPORTED_FONT_EXTENSIONS,
    build_user_font_stem,
    find_font_path,
    find_user_font_path,
    get_available_fonts as list_available_fonts,
    get_user_fonts_dir,
    sanitize_font_stem,
)
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

logger = logging.getLogger(__name__)
router = APIRouter(tags=["media"])
MAX_VIDEO_UPLOAD_BYTES = 1_000_000_000
MAX_FONT_UPLOAD_BYTES = 10 * 1024 * 1024

_BUNDLED_TRANSITIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "transitions"
)
_TRANSITION_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _ffmpeg_bin_dir() -> Path:
    """Resolve the configured ffmpeg bin directory (FFMPEG_BIN_DIR)."""
    return Path(get_config().ffmpeg_bin_dir)


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


async def _write_upload_to_disk(
    uploaded_file: UploadFile,
    target_path: Path,
    max_bytes: int,
) -> None:
    chunk_size = 1024 * 1024
    written = 0

    try:
        async with aiofiles.open(target_path, "wb") as destination:
            while True:
                chunk = await uploaded_file.read(chunk_size)
                if not chunk:
                    break

                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413, detail="Uploaded file is too large"
                    )

                await destination.write(chunk)
    except Exception:
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        raise


def _transitions_dir() -> Path:
    """Absolute path to the bundled transition MP4 directory."""
    try:
        from ...transition_spec import transitions_dir

        return transitions_dir()
    except Exception as exc:
        logger.warning(
            "transition_spec unavailable (%s); using bundled transitions directory fallback",
            exc,
        )
        return _BUNDLED_TRANSITIONS_DIR


def _builtin_transition_names() -> list[str]:
    """Return built-in xfade transition names, or an empty list when unavailable."""
    try:
        from ...transition_spec import builtin_transition_names

        return builtin_transition_names()
    except Exception as exc:
        logger.warning(
            "transition_spec unavailable (%s); builtin transitions are not listed", exc
        )
        return []


def _transition_limits() -> tuple[float, int]:
    """Return (max duration seconds, max file MB) for uploaded transitions."""
    try:
        from ...transition_spec import (
            MAX_TRANSITION_FILE_SECONDS,
            MAX_TRANSITION_FILE_MB,
        )

        return MAX_TRANSITION_FILE_SECONDS, MAX_TRANSITION_FILE_MB
    except Exception as exc:
        logger.warning(
            "transition_spec unavailable (%s); using default transition limits", exc
        )
        return 1.5, 20


def _title_case_display_name(name: str) -> str:
    """Convert a transition stem/name into a display title."""
    return name.replace("_", " ").replace("-", " ").title()


def _ffprobe_executable() -> Optional[str]:
    """Locate an ffprobe executable (configured install first, then PATH)."""
    bundled = _ffmpeg_bin_dir() / "ffprobe.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("ffprobe")


def _subprocess_env() -> dict[str, str]:
    """Environment with the configured ffmpeg bin directory on PATH."""
    env = dict(os.environ)
    env["PATH"] = str(_ffmpeg_bin_dir()) + os.pathsep + env.get("PATH", "")
    return env


def _probe_transition_duration(file_path: Path) -> Optional[float]:
    """Return the media duration in seconds, or None when it cannot be validated."""
    ffprobe = _ffprobe_executable()
    if ffprobe is None:
        logger.warning("ffprobe not found; skipping duration validation for %s", file_path)
        return None

    command = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, env=_subprocess_env()
        )
    except Exception as exc:
        logger.error("ffprobe failed to run for %s: %s", file_path, exc)
        return None

    if result.returncode != 0:
        logger.error("ffprobe rejected %s: %s", file_path, result.stderr.strip())
        return None

    try:
        return float(result.stdout.strip())
    except ValueError:
        logger.error(
            "ffprobe returned unparsable duration for %s: %r", file_path, result.stdout
        )
        return None


@router.get("/fonts")
async def get_available_fonts_route(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Get list of available fonts."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        if not FONTS_DIR.exists():
            return {"fonts": [], "message": "Fonts directory not found"}

        fonts = list_available_fonts(user_id=user_id)
        logger.info(f"Found {len(fonts)} available fonts")
        return {"fonts": fonts}

    except Exception as e:
        logger.error(f"Error retrieving fonts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving fonts: {str(e)}")


@router.get("/fonts/{font_name}")
async def get_font_file(
    font_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve a specific font file."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        font_path = find_font_path(font_name, user_id=user_id)

        if not font_path:
            raise HTTPException(status_code=404, detail="Font not found")

        media_type = "font/ttf" if font_path.suffix.lower() == ".ttf" else "font/otf"

        return FileResponse(
            path=str(font_path),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=31536000",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving font {font_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving font: {str(e)}")


@router.post("/fonts/upload")
async def upload_font(
    request: Request,
    uploaded_file: UploadFile = File(..., alias="file"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom .ttf/.otf font so it appears in the font picker."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(user_id)
        paid_access = not summary.get("monetization_enabled") or (
            summary.get("plan") in {"pro", "scale"}
            and summary.get("subscription_status") in {"active", "trialing"}
        )
        if not paid_access:
            raise HTTPException(
                status_code=403,
                detail="Custom font uploads are available for paid plans only",
            )

        if not uploaded_file.filename:
            raise HTTPException(status_code=400, detail="Missing file name")

        uploaded_filename = uploaded_file.filename or "font.ttf"
        extension = Path(uploaded_filename).suffix.lower()
        if extension not in SUPPORTED_FONT_EXTENSIONS:
            raise HTTPException(
                status_code=400, detail="Only .ttf and .otf fonts are supported"
            )

        user_fonts_dir = get_user_fonts_dir(user_id)
        user_fonts_dir.mkdir(parents=True, exist_ok=True)

        original_stem = sanitize_font_stem(uploaded_filename)
        stored_stem = build_user_font_stem(user_id, original_stem)
        target_path = user_fonts_dir / f"{stored_stem}{extension}"
        suffix = 2
        while target_path.exists():
            target_path = user_fonts_dir / f"{stored_stem}-{suffix}{extension}"
            suffix += 1

        await _write_upload_to_disk(uploaded_file, target_path, MAX_FONT_UPLOAD_BYTES)

        logger.info(f"Uploaded font: {target_path.name}")

        return {
            "font": {
                "name": target_path.stem,
                "display_name": original_stem.replace("-", " ")
                .replace("_", " ")
                .title(),
                "filename": target_path.name,
                "format": extension.lstrip("."),
                "scope": "user",
            },
            "message": "Font uploaded successfully",
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error uploading font: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading font: {str(e)}")


@router.delete("/fonts/{font_name}")
async def delete_font(
    font_name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a custom font owned by the authenticated user."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        font_path = find_user_font_path(font_name, user_id)

        if font_path is None:
            if find_font_path(font_name) is not None:
                raise HTTPException(
                    status_code=403, detail="Bundled system fonts cannot be deleted"
                )
            raise HTTPException(status_code=404, detail="Custom font not found")

        deleted_name = font_path.stem
        font_path.unlink(missing_ok=True)
        logger.info("Deleted custom font %s for user %s", font_path.name, user_id)
        return {"font_name": deleted_name, "message": "Font deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting font %s: %s", font_name, e)
        raise HTTPException(status_code=500, detail=f"Error deleting font: {str(e)}")


@router.get("/transitions")
async def get_available_transitions():
    """Get the list of available transition effects (builtin xfade + custom files)."""
    try:
        transition_info = [
            {
                "name": name,
                "display_name": _title_case_display_name(name),
                "kind": "builtin",
                "file_path": None,
            }
            for name in _builtin_transition_names()
        ]

        transitions_dir = _transitions_dir()
        if transitions_dir.is_dir():
            for transition_path in sorted(transitions_dir.glob("*.mp4")):
                transition_info.append(
                    {
                        "name": transition_path.stem,
                        "display_name": _title_case_display_name(transition_path.stem),
                        "kind": "file",
                        "file_path": str(transition_path),
                    }
                )

        logger.info("Found %d available transitions", len(transition_info))
        return {"transitions": transition_info}

    except Exception as e:
        logger.error("Error retrieving transitions: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Error retrieving transitions: {str(e)}"
        )


@router.get("/transitions/{name}/file")
async def get_transition_file(name: str):
    """Serve a custom transition MP4 file by its safe stem name."""
    if not _TRANSITION_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="Transition not found")

    transition_path = _transitions_dir() / f"{name}.mp4"
    if not transition_path.is_file():
        raise HTTPException(status_code=404, detail="Transition not found")

    return FileResponse(
        path=str(transition_path),
        media_type="video/mp4",
        headers={
            "Cache-Control": "public, max-age=31536000",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/transitions/upload")
async def upload_transition(
    request: Request,
    uploaded_file: UploadFile = File(..., alias="file"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom transition MP4 (admin only) into the transitions directory."""
    await require_admin_user(request, db, get_config())

    if not uploaded_file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    uploaded_filename = uploaded_file.filename or "transition.mp4"
    if Path(uploaded_filename).suffix.lower() != ".mp4":
        raise HTTPException(
            status_code=400, detail="Only .mp4 transition files are supported"
        )

    slug = re.sub(r"[^a-z0-9_-]", "", Path(uploaded_filename).stem.lower())
    if not slug:
        raise HTTPException(status_code=400, detail="Invalid transition file name")

    max_transition_seconds, max_transition_mb = _transition_limits()
    max_transition_bytes = max_transition_mb * 1024 * 1024

    config = get_config()
    upload_temp_dir = Path(config.temp_dir) / "transition_uploads"
    upload_temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_temp_dir / f"{uuid.uuid4().hex}.mp4"

    try:
        await _write_upload_to_disk(uploaded_file, temp_path, max_transition_bytes)

        duration = _probe_transition_duration(temp_path)
        if duration is None:
            raise HTTPException(
                status_code=400, detail="Invalid or unreadable MP4 transition file"
            )
        if duration > max_transition_seconds:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Transition duration {duration:.2f}s exceeds the "
                    f"{max_transition_seconds}s limit"
                ),
            )

        target_path = _transitions_dir() / f"{slug}.mp4"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, target_path)

        logger.info("Uploaded transition %s (%.2fs)", target_path.name, duration)
        return {
            "transition": {
                "name": slug,
                "display_name": slug.title(),
                "kind": "file",
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error uploading transition: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Error uploading transition: {str(e)}"
        )
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("/caption-templates")
async def get_caption_templates():
    """Get available caption templates.

    Returns a stable default list if optional template module is unavailable.
    """
    default_templates = [
        {
            "id": "default",
            "name": "Default",
            "description": "Clean subtitle style",
            "animation": "none",
            "font_family": "TikTokSans-Regular",
            "font_size": 24,
            "font_color": "#FFFFFF",
        }
    ]

    try:
        from ...caption_templates import get_template_info

        templates = get_template_info()
        return {"templates": templates or default_templates}
    except Exception:
        return {"templates": default_templates}


@router.get("/broll/status")
async def get_broll_status():
    """Return whether B-roll integrations are configured."""
    config = get_config()
    return {
        "configured": bool(config.pexels_api_key),
        "provider": "pexels" if config.pexels_api_key else None,
    }


@router.post("/upload")
async def upload_video(request: Request, db: AsyncSession = Depends(get_db)):
    """Upload a video to the server."""
    try:
        await _get_authenticated_user_id(request, db)
        config = get_config()

        # Get the form data
        form_data = await request.form()
        video_file = cast(Any, form_data.get("video"))

        if not getattr(video_file, "filename", None) or not hasattr(video_file, "read"):
            raise HTTPException(status_code=400, detail="No video file provided")

        upload = cast(UploadFile, video_file)
        upload_filename = upload.filename or "upload.mp4"

        # Create uploads directory
        uploads_dir = Path(config.temp_dir) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename
        file_extension = Path(upload_filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        video_path = uploads_dir / unique_filename

        # Save the uploaded file
        await _write_upload_to_disk(upload, video_path, MAX_VIDEO_UPLOAD_BYTES)

        logger.info(f"✅ Video uploaded successfully to: {video_path}")

        return {
            "message": "Video uploaded successfully",
            "video_path": f"upload://{unique_filename}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error uploading video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading video: {str(e)}")
