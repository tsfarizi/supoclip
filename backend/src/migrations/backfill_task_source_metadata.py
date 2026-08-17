"""Backfill Redis-only task source metadata into the Schema v2 tasks columns.

For every task that has a ``task_source:{task_id}`` key in Redis, copy
``output_format`` / ``add_subtitles`` / cleanup settings into the matching
``tasks`` row.

Idempotency contract: a column is written only while it still holds its
post-migration placeholder (the column default for NOT NULL columns, NULL for
cleanup_settings_json). Values already persisted by a previous backfill run or
by the application are never overwritten. Rows without a matching Redis key and
tasks missing from the database are skipped.

Run after backend/src/migrations/sql/20260811_0001_schema_v2.sql:
    uv run python src/migrations/backfill_task_source_metadata.py
Point DATABASE_URL at the target database to backfill a different one.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.clip_cleanup import normalize_clip_cleanup_settings  # noqa: E402
from src.video_utils import VALID_OUTPUT_FORMATS  # noqa: E402

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://localhost:5432/supoclip"
)
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL.split("://", 1)[1]
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

TASK_SOURCE_KEY_PREFIX = "task_source:"
DEFAULT_OUTPUT_FORMAT = "vertical"
DEFAULT_ADD_SUBTITLES = True

# Placeholder guards mirror the migration's column defaults; only rows still
# carrying them are eligible for backfill.
GUARD_OUTPUT_FORMAT = text(
    "UPDATE tasks SET output_format = :value "
    "WHERE id = :task_id AND output_format = 'vertical'"
)
GUARD_ADD_SUBTITLES = text(
    "UPDATE tasks SET add_subtitles = :value "
    "WHERE id = :task_id AND add_subtitles = TRUE"
)
GUARD_CLEANUP_SETTINGS = text(
    "UPDATE tasks SET cleanup_settings_json = CAST(:value AS jsonb) "
    "WHERE id = :task_id AND cleanup_settings_json IS NULL"
)


def _parse_payload(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _extract_output_format(payload: dict) -> str | None:
    value = payload.get("output_format", DEFAULT_OUTPUT_FORMAT)
    if value not in VALID_OUTPUT_FORMATS:
        return None
    return value


def _extract_add_subtitles(payload: dict) -> bool | None:
    value = payload.get("add_subtitles", DEFAULT_ADD_SUBTITLES)
    if not isinstance(value, bool):
        return None
    return value


def _extract_cleanup_settings(payload: dict) -> dict:
    return normalize_clip_cleanup_settings(
        payload.get("cut_long_pauses"),
        payload.get("pause_threshold_ms"),
        payload.get("remove_filler_words"),
        payload.get("filtered_words"),
    )


async def _backfill_task(conn, task_id: str, payload: dict) -> dict:
    written = {}
    output_format = _extract_output_format(payload)
    if output_format is not None:
        result = await conn.execute(
            GUARD_OUTPUT_FORMAT, {"value": output_format, "task_id": task_id}
        )
        written["output_format"] = result.rowcount
    add_subtitles = _extract_add_subtitles(payload)
    if add_subtitles is not None:
        result = await conn.execute(
            GUARD_ADD_SUBTITLES, {"value": add_subtitles, "task_id": task_id}
        )
        written["add_subtitles"] = result.rowcount
    cleanup_settings = _extract_cleanup_settings(payload)
    result = await conn.execute(
        GUARD_CLEANUP_SETTINGS,
        {"value": json.dumps(cleanup_settings), "task_id": task_id},
    )
    written["cleanup_settings_json"] = result.rowcount
    return written


async def main() -> int:
    engine = create_async_engine(DATABASE_URL)
    redis_client = Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    try:
        await redis_client.ping()
    except Exception as exc:
        print(
            f"[backfill] Redis unreachable at {REDIS_HOST}:{REDIS_PORT}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        keys = [
            key async for key in redis_client.scan_iter(match=f"{TASK_SOURCE_KEY_PREFIX}*")
        ]
        print(f"[backfill] Redis task_source keys: {len(keys)}")

        updated_rows = 0
        missing_tasks = 0
        invalid_payloads = 0
        touched = {"output_format": 0, "add_subtitles": 0, "cleanup_settings_json": 0}

        async with engine.begin() as conn:
            for key in keys:
                task_id = key[len(TASK_SOURCE_KEY_PREFIX):]
                payload = _parse_payload(await redis_client.get(key))
                if payload is None:
                    invalid_payloads += 1
                    print(f"[backfill] skip {task_id}: missing or invalid JSON payload")
                    continue
                written = await _backfill_task(conn, task_id, payload)
                columns_written = sum(1 for rowcount in written.values() if rowcount > 0)
                if columns_written == 0:
                    missing_tasks += 1
                    print(f"[backfill] skip {task_id}: no task row or columns already set")
                    continue
                updated_rows += 1
                for column, rowcount in written.items():
                    touched[column] += rowcount
                print(f"[backfill] wrote {task_id}: {written}")

        print(f"[backfill] tasks updated: {updated_rows}")
        print(f"[backfill] tasks skipped (missing/untouched): {missing_tasks}")
        print(f"[backfill] invalid payloads skipped: {invalid_payloads}")
        print(f"[backfill] columns touched: {touched}")
        print(f"[backfill] database: {DATABASE_URL}")
        return 0
    finally:
        await redis_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
