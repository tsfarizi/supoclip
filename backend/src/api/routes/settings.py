"""
Settings API routes for SupoClip runtime and system settings.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import require_admin_user
from ...config import get_config
from ...database import get_db
from ...shared.config.runtime_settings import (
    MIN_RENDER_CONCURRENCY,
    MAX_RENDER_CONCURRENCY,
    get_render_concurrency,
    set_render_concurrency,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class RenderConcurrencyPayload(BaseModel):
    render_concurrency: int = Field(
        ...,
        ge=MIN_RENDER_CONCURRENCY,
        le=MAX_RENDER_CONCURRENCY,
        description=f"Max concurrent render processes ({MIN_RENDER_CONCURRENCY}-{MAX_RENDER_CONCURRENCY})",
    )


@router.get("/render-concurrency")
async def get_render_concurrency_setting(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the current render concurrency setting."""
    return {"render_concurrency": get_render_concurrency()}


@router.put("/render-concurrency")
async def update_render_concurrency_setting(
    payload: RenderConcurrencyPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update the render concurrency setting (requires admin user)."""
    user_id = await require_admin_user(request, db, get_config())
    try:
        updated = await set_render_concurrency(db, payload.render_concurrency, updated_by=user_id)
        return {"render_concurrency": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
