"""Freesound API v2 provider for openly licensed MP3 sound previews."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from .config import get_config

logger = logging.getLogger(__name__)

FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/"
FREESOUND_SEARCH_TIMEOUT_SECONDS = 15.0
FREESOUND_DOWNLOAD_TIMEOUT_SECONDS = 60.0
FREESOUND_MAX_RETRIES = 2
FREESOUND_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
FREESOUND_FIELDS = (
    "id,name,username,license,previews,duration,type,filesize,url,"
    "gen_ai_preference,score"
)
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
ALLOWED_MIME_TYPES = {"audio/mpeg", "audio/mp3"}


class FreesoundSound(BaseModel):
    """Internal representation of the Freesound search contract."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    username: str
    license: str
    previews: dict[str, str]
    duration: float
    type: str
    filesize: int | None
    url: str
    gen_ai_preference: str | None
    score: float | None

    @property
    def mp3_preview_url(self) -> str | None:
        return self.previews.get("preview-hq-mp3") or self.previews.get("preview-lq-mp3")


class FreesoundProviderError(RuntimeError):
    """Raised when a Freesound request or response violates the provider contract."""


_ALLOWED_LICENSES = frozenset(
    {
        "Creative Commons 0",
        "Attribution",
        "http://creativecommons.org/publicdomain/zero/1.0/",
        "https://creativecommons.org/publicdomain/zero/1.0/",
        "http://creativecommons.org/licenses/by/3.0/",
        "https://creativecommons.org/licenses/by/3.0/",
    }
)


def _is_allowed_license(value: str) -> bool:
    return value in _ALLOWED_LICENSES


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> httpx.Response:
    for attempt in range(FREESOUND_MAX_RETRIES + 1):
        try:
            response = await client.get(url, params=params, headers=headers, timeout=timeout)
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            if attempt >= FREESOUND_MAX_RETRIES:
                raise FreesoundProviderError("Freesound request failed") from error
            await asyncio.sleep((2**attempt) + random.uniform(0.0, 0.25))
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= FREESOUND_MAX_RETRIES:
            return response

        delay = _retry_after_seconds(response)
        await asyncio.sleep(delay if delay is not None else (2**attempt) + random.uniform(0.0, 0.25))

    raise FreesoundProviderError("Freesound request failed")


def _translate_sound(payload: dict[str, Any]) -> FreesoundSound | None:
    try:
        sound = FreesoundSound.model_validate(payload)
    except Exception as error:
        logger.warning("Discarding malformed Freesound result: %s", type(error).__name__)
        return None
    if not _is_allowed_license(sound.license) or not sound.mp3_preview_url:
        return None
    return sound


async def search_freesound_sounds(query: str, *, limit: int = 10) -> list[FreesoundSound]:
    """Search Freesound and return only CC0/Attribution sounds with MP3 previews."""
    api_key = get_config().freesound_api_key
    if not api_key:
        logger.warning("Freesound API key not configured")
        return []
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Freesound search query must not be empty")
    if not 1 <= limit <= 150:
        raise ValueError("Freesound search limit must be between 1 and 150")

    params = {"query": normalized_query, "fields": FREESOUND_FIELDS, "page_size": limit}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await _get_with_retries(
            client, FREESOUND_SEARCH_URL, params=params,
            headers={"Authorization": f"Token {api_key}"},
            timeout=FREESOUND_SEARCH_TIMEOUT_SECONDS,
        )
    if response.status_code != 200:
        logger.warning("Freesound search returned HTTP %s", response.status_code)
        return []
    try:
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else []
    except ValueError as error:
        raise FreesoundProviderError("Freesound returned invalid JSON") from error
    return [sound for item in results if isinstance(item, dict) if (sound := _translate_sound(item))]


async def download_freesound_mp3(sound: FreesoundSound, output_path: Path) -> Path:
    """Download the selected MP3 preview after validating MIME type and size."""
    preview_url = sound.mp3_preview_url
    if not preview_url:
        raise FreesoundProviderError("Freesound sound has no MP3 preview")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path = output_path.with_suffix(".mp3")
    temporary = output_path.with_suffix(".download")
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for attempt in range(FREESOUND_MAX_RETRIES + 1):
                try:
                    async with client.stream(
                        "GET", preview_url,
                        headers={"Authorization": f"Token {get_config().freesound_api_key}"},
                        timeout=FREESOUND_DOWNLOAD_TIMEOUT_SECONDS,
                    ) as response:
                        if response.status_code in RETRYABLE_STATUS_CODES and attempt < FREESOUND_MAX_RETRIES:
                            retry_delay = _retry_after_seconds(response)
                            await asyncio.sleep(retry_delay if retry_delay is not None else (2**attempt) + random.uniform(0.0, 0.25))
                            continue
                        if response.status_code != 200:
                            raise FreesoundProviderError(f"Freesound preview returned HTTP {response.status_code}")
                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if content_type not in ALLOWED_MIME_TYPES:
                            raise FreesoundProviderError("Freesound preview is not an MP3")
                        declared = response.headers.get("Content-Length")
                        if declared and int(declared) > FREESOUND_MAX_DOWNLOAD_BYTES:
                            raise FreesoundProviderError("Freesound preview exceeds the size limit")
                        total = 0
                        with temporary.open("wb") as target:
                            async for block in response.aiter_bytes(1024 * 1024):
                                total += len(block)
                                if total > FREESOUND_MAX_DOWNLOAD_BYTES:
                                    raise FreesoundProviderError("Freesound preview exceeds the size limit")
                                target.write(block)
                        break
                except (httpx.TimeoutException, httpx.NetworkError) as error:
                    if attempt >= FREESOUND_MAX_RETRIES:
                        raise FreesoundProviderError("Freesound request failed") from error
                    await asyncio.sleep((2**attempt) + random.uniform(0.0, 0.25))
            else:
                raise FreesoundProviderError("Freesound request failed")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path
