"""T4 (P0): Alembic migration authority falsification.

Contract clauses under test:
  1. ``init_db``/``alembic upgrade head`` on an EMPTY database converges to the
     full head schema (all expected tables and key columns present).
  2. Running ``upgrade head`` twice is a no-op (idempotency).
  3. Upgrading a POPULATED database preserves every row: the
     ``tasks.generated_clips_ids`` array column is dropped while
     ``generated_clips`` rows survive, and ``tasks.source_identity`` is
     backfilled for non-terminal rows only.
  4. Downgrade round-trip 0004 -> 0003 -> 0002 -> 0001 -> upgrade head runs
     without error and the seeded data survives.
  5. Schema DRIFT check (a): SQLAlchemy ``Base.metadata`` vs the live DB.
     Every model column must exist in the DB; extra DB columns are allowed only
     when migration-managed (documented per table).
  6. Schema DRIFT check (b): every column in frontend/prisma/schema.prisma must
     exist in the DB (Prisma is a subset), and the User model written in both
     Prisma and the backend must agree on core columns.

Mechanics: each migration test builds an isolated scratch database
(``supoclip_t4_*``) owned by the ``supoclip`` role via the psql admin superuser,
runs the real Alembic CLI in a subprocess against it, and drops the database in
a finally block. The CLI is exercised exactly as run.ps1 does (``alembic
upgrade head`` with DATABASE_URL pointed at the target database).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
PSQL_EXE = Path(r"C:\Program Files\PostgreSQL\18\bin\psql.exe")
ADMIN_ARGS = ["-U", "postgres", "-h", "localhost", "-p", "5433"]
ADMIN_PASSWORD = "postgres"
SCRATCH_USER = "supoclip"
SCRATCH_PASSWORD = "supoclip_password"
ALEMBIC_INI = BACKEND_DIR / "src" / "migrations" / "alembic.ini"
PRISMA_SCHEMA = REPO_ROOT / "frontend" / "prisma" / "schema.prisma"

# Expected head schema (revision 0004). Table/column expectations are written
# from the migration snapshot (0001..0004) plus the SQLAlchemy models, NOT from
# the live database, so this file itself would catch a drift in the dev DB.
EXPECTED_TABLES = {
    "tasks",
    "users",
    "sources",
    "generated_clips",
    "processing_cache",
    "app_settings",
    "api_keys",
    "session",
    "account",
    "verification",
    "stripe_webhook_events",
    "revenuecat_webhook_events",
}

EXPECTED_COLUMNS = {
    "tasks": {
        "id", "user_id", "source_id", "source_identity", "status", "progress",
        "progress_message", "font_family", "font_size", "font_color",
        "caption_template", "include_broll", "sound_effects_count",
        "processing_mode", "started_at", "completed_at", "cache_hit",
        "error_code", "stage_timings_json", "completion_notification_sent_at",
        "share_token", "share_enabled", "created_at", "updated_at",
        "output_format", "add_subtitles", "cleanup_settings_json",
        "hook_persist", "watermark", "watermark_persist",
    },
    "users": {
        "id", "name", "email", "emailVerified", "image", "createdAt",
        "updatedAt", "first_name", "last_name", "password_hash",
        "notify_on_completion", "is_admin", "plan", "subscription_status",
        "subscription_provider", "stripe_customer_id", "stripe_subscription_id",
        "billing_period_start", "billing_period_end", "trial_ends_at",
        "default_font_family", "default_font_size", "default_font_color",
    },
    "sources": {"id", "type", "title", "url", "created_at", "updated_at"},
    "generated_clips": {
        "id", "task_id", "filename", "file_path", "start_time", "end_time",
        "duration", "text", "relevance_score", "reasoning", "clip_order",
        "virality_score", "hook_score", "engagement_score", "value_score",
        "shareability_score", "hook_type", "hook_title", "created_at",
        "updated_at",
    },
    "processing_cache": {
        "cache_key", "source_url", "source_type", "video_path",
        "transcript_text", "analysis_json", "sound_effects_count", "created_at",
        "updated_at",
    },
    "app_settings": {
        "setting_key", "encrypted_value", "prefer_admin_value", "updated_by",
        "created_at", "updated_at",
    },
    "api_keys": {
        "id", "user_id", "name", "key_hash", "key_prefix", "created_at",
        "last_used_at", "revoked_at",
    },
    "session": {
        "id", "expiresAt", "token", "createdAt", "updatedAt", "ipAddress",
        "userAgent", "userId",
    },
    "account": {
        "id", "accountId", "providerId", "userId", "accessToken",
        "refreshToken", "idToken", "accessTokenExpiresAt",
        "refreshTokenExpiresAt", "scope", "password", "createdAt", "updatedAt",
    },
    "verification": {
        "id", "identifier", "value", "expiresAt", "createdAt", "updatedAt",
    },
    "stripe_webhook_events": {"id", "type", "created_at"},
    "revenuecat_webhook_events": {"id", "type", "created_at"},
}

# Extra DB columns on model tables that are NOT declared in src/models.py.
# Every one of these is added by migration 0001 (the guarded snapshot's
# _migration_gap / _schema_v2 sections). The drift check allows exactly these.
MIGRATION_MANAGED_EXTRA_COLUMNS = {
    "users": {"default_font_family", "default_font_size", "default_font_color"},
    "tasks": {
        "output_format", "add_subtitles", "cleanup_settings_json",
        "hook_persist", "watermark", "watermark_persist",
    },
    "generated_clips": {"hook_title"},
}

# Legacy tables that exist in the live DB but are not SQLAlchemy models and not
# Alembic-managed. schema_migrations is the retired SQL-runner ledger; it is
# allowed to linger and is documented as known, not asserted against.
LEGACY_UNMODELED_TABLES = {"schema_migrations"}

# Migration-managed tables (created by revision 0001) that have no SQLAlchemy
# model: the auth/session/webhook ledger consumed by Prisma and admin routes.
MIGRATION_MANAGED_NON_MODEL_TABLES = {
    "session",
    "account",
    "verification",
    "api_keys",
    "stripe_webhook_events",
}


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _scratch_db_name() -> str:
    return f"supoclip_t4_{uuid4().hex[:10]}"


def _scratch_url(db_name: str) -> str:
    return (
        f"postgresql+asyncpg://{SCRATCH_USER}:{SCRATCH_PASSWORD}"
        f"@localhost:5433/{db_name}"
    )


def _psql_admin(sql: str, *, dbname: str | None = None) -> subprocess.CompletedProcess:
    """Run one SQL statement as the postgres superuser on port 5433."""
    cmd = [str(PSQL_EXE), *ADMIN_ARGS, "-v", "ON_ERROR_STOP=1", "-t", "-A"]
    if dbname:
        cmd += ["-d", dbname]
    cmd += ["-c", sql]
    env = dict(os.environ, PGPASSWORD=ADMIN_PASSWORD)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _psql_scratch(
    db_name: str, sql: str, *, password: str = SCRATCH_PASSWORD
) -> subprocess.CompletedProcess:
    """Run SQL against the scratch database as the supoclip role."""
    cmd = [
        str(PSQL_EXE), *ADMIN_ARGS, "-v", "ON_ERROR_STOP=1", "-t", "-A",
        "-d", db_name, "-c", sql,
    ]
    env = dict(os.environ, PGPASSWORD=password)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _create_scratch_db(db_name: str) -> None:
    _psql_admin(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE);')
    result = _psql_admin(f'CREATE DATABASE "{db_name}" OWNER {SCRATCH_USER};')
    assert result.returncode == 0, (
        f"CREATE DATABASE {db_name} failed: {result.stderr}"
    )


def _drop_scratch_db(db_name: str) -> None:
    _psql_admin(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE);')


def _run_alembic(db_name: str, *args: str) -> subprocess.CompletedProcess:
    """Run the real Alembic CLI against the scratch database."""
    env = dict(os.environ)
    env["DATABASE_URL"] = _scratch_url(db_name)
    result = subprocess.run(
        [
            sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed (rc={result.returncode}):\n"
        f"{result.stderr[-2000:]}"
    )
    return result


def _assert_scratch_ok(db_name: str) -> None:
    """Fail loudly when a scratch psql command did not execute cleanly."""
    result = _psql_scratch(db_name, "SELECT 1;")
    assert result.returncode == 0, f"scratch db connection failed: {result.stderr}"


async def _inspect_schema(db_name: str) -> dict[str, set[str]]:
    """Return {table: {column,...}} for the scratch database via SQLAlchemy."""
    engine = create_async_engine(_scratch_url(db_name), poolclass=NullPool)
    try:

        def _collect(sync_conn):
            insp = inspect(sync_conn)
            return {
                table: {col["name"] for col in insp.get_columns(table)}
                for table in insp.get_table_names()
            }

        async with engine.connect() as conn:
            return await conn.run_sync(_collect)
    finally:
        await engine.dispose()


async def _inspect_engine(engine) -> dict[str, set[str]]:
    """Same inspection against an already-open async engine (live DB)."""
    async with engine.connect() as conn:

        def _collect(sync_conn):
            insp = inspect(sync_conn)
            return {
                table: {col["name"] for col in insp.get_columns(table)}
                for table in insp.get_table_names()
            }

        return await conn.run_sync(_collect)


def _scratch_count(db_name: str, sql: str) -> str:
    result = _psql_scratch(db_name, sql)
    assert result.returncode == 0, f"count query failed: {result.stderr}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. Empty database: converge + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upgrade_empty_database_converges_to_head_and_is_idempotent():
    db_name = _scratch_db_name()
    _create_scratch_db(db_name)
    try:
        _run_alembic(db_name, "upgrade", "head")
        schema = await _inspect_schema(db_name)

        # Every expected table exists.
        missing_tables = EXPECTED_TABLES - set(schema)
        assert not missing_tables, f"missing tables after upgrade: {missing_tables}"

        # Every expected key column exists per table.
        for table, expected_cols in EXPECTED_COLUMNS.items():
            actual_cols = schema.get(table, set())
            missing_cols = expected_cols - actual_cols
            assert not missing_cols, (
                f"table {table} missing columns after upgrade: {missing_cols}"
            )

        # generated_clips_ids must be gone at head (revision 0002 dropped it).
        assert "generated_clips_ids" not in schema["tasks"], (
            "tasks.generated_clips_ids must be dropped at head"
        )
        # source_identity must exist at head (revision 0004 added it).
        assert "source_identity" in schema["tasks"], (
            "tasks.source_identity missing at head"
        )

        # Head version stamped.
        assert _scratch_count(db_name, "SELECT version_num FROM alembic_version;") == "0004"

        # Idempotency: a second upgrade head is a no-op and stays at head.
        _run_alembic(db_name, "upgrade", "head")
        schema_after = await _inspect_schema(db_name)
        assert schema_after == schema, "second upgrade head changed the schema"
        assert _scratch_count(db_name, "SELECT version_num FROM alembic_version;") == "0004"
    finally:
        _drop_scratch_db(db_name)


# ---------------------------------------------------------------------------
# 2. Populated database: row preservation + generated_clips_ids drop +
#    source_identity backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upgrade_populated_database_preserves_rows_and_backfills():
    db_name = _scratch_db_name()
    _create_scratch_db(db_name)
    try:
        # Converge to 0001 first (schema still has tasks.generated_clips_ids),
        # then seed rows that predate revisions 0002/0004.
        _run_alembic(db_name, "upgrade", "0001")

        seed_sql = """
            INSERT INTO users (id, name, email, "emailVerified", "createdAt", "updatedAt")
            VALUES ('u1', 'Migr User', 'u1@example.com', false, NOW(), NOW());
            INSERT INTO sources (id, type, title, url, created_at, updated_at)
            VALUES ('s1', 'youtube', 'Migr Source', 'https://www.youtube.com/watch?v=migr123', NOW(), NOW());
            INSERT INTO tasks (id, user_id, source_id, generated_clips_ids, status, created_at, updated_at)
            VALUES ('t1', 'u1', 's1', ARRAY['c1']::VARCHAR(36)[], 'processing', NOW(), NOW());
            INSERT INTO tasks (id, user_id, source_id, generated_clips_ids, status, created_at, updated_at)
            VALUES ('t2', 'u1', 's1', ARRAY['c1']::VARCHAR(36)[], 'completed', NOW(), NOW());
            INSERT INTO generated_clips (id, task_id, filename, file_path, start_time, end_time, duration, relevance_score, clip_order, created_at, updated_at)
            VALUES ('c1', 't1', 'clip.mp4', '/tmp/clip.mp4', '00:00', '00:10', 10.0, 0.9, 1, NOW(), NOW());
        """
        result = _psql_scratch(db_name, seed_sql)
        assert result.returncode == 0, f"seed failed: {result.stderr}"
        _assert_scratch_ok(db_name)

        assert _scratch_count(
            db_name,
            "SELECT (SELECT count(*) FROM users)||'/'||(SELECT count(*) FROM sources)||'/'||"
            "(SELECT count(*) FROM tasks)||'/'||(SELECT count(*) FROM generated_clips);",
        ) == "1/1/2/1"

        # Upgrade to head: 0002 drops the array column, 0004 backfills.
        _run_alembic(db_name, "upgrade", "head")

        # Row counts survive the upgrade.
        assert _scratch_count(
            db_name,
            "SELECT (SELECT count(*) FROM users)||'/'||(SELECT count(*) FROM sources)||'/'||"
            "(SELECT count(*) FROM tasks)||'/'||(SELECT count(*) FROM generated_clips);",
        ) == "1/1/2/1"

        # The denormalized array column is gone while the clips table rows stay.
        assert _scratch_count(
            db_name,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='tasks' AND column_name='generated_clips_ids';",
        ) == "0"
        assert _scratch_count(db_name, "SELECT count(*) FROM generated_clips;") == "1"

        # Backfill: non-terminal task t1 got youtube:<video_id>; terminal task
        # t2 (completed) was skipped and stays NULL.
        identity_t1 = _scratch_count(
            db_name, "SELECT source_identity FROM tasks WHERE id='t1';"
        )
        assert identity_t1 == "youtube:migr123", (
            f"expected backfill youtube:migr123, got {identity_t1!r}"
        )
        identity_t2 = _scratch_count(
            db_name, "SELECT source_identity FROM tasks WHERE id='t2';"
        )
        assert identity_t2 == "", f"terminal task must not be backfilled: {identity_t2!r}"
    finally:
        _drop_scratch_db(db_name)


# ---------------------------------------------------------------------------
# 3. Downgrade round-trip 0004 -> 0003 -> 0002 -> 0001 -> upgrade head
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_downgrade_round_trip_preserves_data():
    db_name = _scratch_db_name()
    _create_scratch_db(db_name)
    try:
        _run_alembic(db_name, "upgrade", "head")

        seed_sql = """
            INSERT INTO users (id, name, email, "emailVerified", "createdAt", "updatedAt")
            VALUES ('u1', 'Migr User', 'u1@example.com', false, NOW(), NOW());
            INSERT INTO sources (id, type, title, url, created_at, updated_at)
            VALUES ('s1', 'youtube', 'Migr Source', 'https://www.youtube.com/watch?v=roundtrip9', NOW(), NOW());
            INSERT INTO tasks (id, user_id, source_id, status, created_at, updated_at)
            VALUES ('t1', 'u1', 's1', 'processing', NOW(), NOW());
            INSERT INTO tasks (id, user_id, source_id, status, created_at, updated_at)
            VALUES ('t2', 'u1', 's1', 'completed', NOW(), NOW());
            INSERT INTO generated_clips (id, task_id, filename, file_path, start_time, end_time, duration, relevance_score, clip_order, created_at, updated_at)
            VALUES ('c1', 't1', 'clip.mp4', '/tmp/clip.mp4', '00:00', '00:10', 10.0, 0.9, 1, NOW(), NOW());
        """
        result = _psql_scratch(db_name, seed_sql)
        assert result.returncode == 0, f"seed failed: {result.stderr}"

        # Step through each downgrade revision.
        _run_alembic(db_name, "downgrade", "0003")
        _run_alembic(db_name, "downgrade", "0002")
        _run_alembic(db_name, "downgrade", "0001")

        # Data survives the full downgrade.
        assert _scratch_count(
            db_name,
            "SELECT (SELECT count(*) FROM users)||'/'||(SELECT count(*) FROM sources)||'/'||"
            "(SELECT count(*) FROM tasks)||'/'||(SELECT count(*) FROM generated_clips);",
        ) == "1/1/2/1"

        # And survives the re-upgrade back to head.
        _run_alembic(db_name, "upgrade", "head")
        assert _scratch_count(
            db_name,
            "SELECT (SELECT count(*) FROM users)||'/'||(SELECT count(*) FROM sources)||'/'||"
            "(SELECT count(*) FROM tasks)||'/'||(SELECT count(*) FROM generated_clips);",
        ) == "1/1/2/1"
        assert _scratch_count(db_name, "SELECT version_num FROM alembic_version;") == "0004"
        # The non-terminal row was re-backfilled after the column round-trip.
        assert _scratch_count(
            db_name, "SELECT source_identity FROM tasks WHERE id='t1';"
        ) == "youtube:roundtrip9"
    finally:
        _drop_scratch_db(db_name)


# ---------------------------------------------------------------------------
# 4. Drift check (a): SQLAlchemy models vs live DB
# ---------------------------------------------------------------------------


def _model_tables_and_columns() -> dict[str, set[str]]:
    from src.models import Base

    return {
        table.name: {col.name for col in table.columns}
        for table in Base.metadata.sorted_tables
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_drift_sqlalchemy_metadata_vs_live_db(initialized_database):
    from src.models import Base  # noqa: F401  (ensures all models registered)

    model_tables = _model_tables_and_columns()
    db_schema = await _inspect_engine(initialized_database)

    # Every model table must exist in the live DB.
    missing_tables = set(model_tables) - set(db_schema)
    assert not missing_tables, f"model tables missing from DB: {missing_tables}"

    # Every model column must exist in the live DB.
    for table, model_cols in model_tables.items():
        db_cols = db_schema.get(table, set())
        missing_cols = model_cols - db_cols
        assert not missing_cols, (
            f"table {table}: model columns missing from DB: {missing_cols}"
        )

    # Extra DB columns are allowed only when migration-managed (0001 snapshot
    # additions). Any other extra column is real drift and must fail.
    documented = MIGRATION_MANAGED_EXTRA_COLUMNS
    for table, db_cols in db_schema.items():
        if table not in model_tables:
            continue
        extra = db_cols - model_tables[table]
        allowed = documented.get(table, set())
        assert extra == allowed, (
            f"table {table}: undocumented extra DB columns: {extra - allowed}; "
            f"documented migration-managed extras missing: {allowed - extra}"
        )

    # Every model table column set is fully represented; model tables have no
    # leftover unknown extras (covered above). Sanity: the head-only columns
    # added by the migrations exist in the live DB.
    assert "source_identity" in db_schema["tasks"]
    assert "generated_clips_ids" not in db_schema["tasks"]

    # Document legacy and migration-managed non-model tables that may exist.
    unmodeled = set(db_schema) - set(model_tables)
    assert unmodeled == (
        {"alembic_version"}
        | MIGRATION_MANAGED_NON_MODEL_TABLES
        | LEGACY_UNMODELED_TABLES
    ), f"unexpected non-model tables: {unmodeled}"


# ---------------------------------------------------------------------------
# 5. Drift check (b): Prisma schema vs live DB
# ---------------------------------------------------------------------------

_PRISMA_SCALAR_TYPES = {
    "String", "Boolean", "Int", "BigInt", "Float", "Decimal", "DateTime",
    "Json", "Bytes",
}


def _parse_prisma_schema(path: Path) -> dict[str, set[str]]:
    """Parse model blocks from schema.prisma into {db_table: scalar columns}.

    Only scalar fields become DB columns; relation fields (ending in ``[]`` or
    carrying ``@relation``) and ``@@`` directives are skipped. Table names come
    from ``@@map``.
    """
    models: dict[str, set[str]] = {}
    current_model: str | None = None
    current_table: str | None = None
    current_fields: set[str] = set()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("model ") and line.endswith("{"):
            current_model = line[len("model ") : -1].strip()
            current_table = None
            current_fields = set()
            continue
        if line == "}":
            if current_model and current_table:
                models[current_table] = current_fields
            current_model = None
            current_table = None
            current_fields = set()
            continue
        if current_model is None:
            continue
        map_match = re.match(r'@@map\("([^"]+)"\)', line)
        if map_match:
            current_table = map_match.group(1)
            continue
        if line.startswith("@@"):
            continue
        field_match = re.match(r"([A-Za-z_]\w*)\s+(\w+)", line)
        if not field_match:
            continue
        name, type_token = field_match.group(1), field_match.group(2)
        if type_token.endswith("]") or "@relation" in line:
            continue
        if type_token in _PRISMA_SCALAR_TYPES or "@db." in line:
            current_fields.add(name)
    return models


# Core columns that both the backend User model and the Prisma User model must
# agree on (P0 drift check for the dual-written User table).
USER_CORE_COLUMNS = {
    "id", "name", "email", "emailVerified", "image", "createdAt", "updatedAt",
    "first_name", "last_name", "password_hash", "notify_on_completion",
    "is_admin", "plan", "subscription_status", "subscription_provider",
    "stripe_customer_id", "stripe_subscription_id", "billing_period_start",
    "billing_period_end", "trial_ends_at",
}


@pytest.mark.asyncio(loop_scope="session")
async def test_drift_prisma_schema_vs_live_db(initialized_database):
    assert PRISMA_SCHEMA.is_file(), f"prisma schema not found: {PRISMA_SCHEMA}"
    prisma_cols = _parse_prisma_schema(PRISMA_SCHEMA)
    assert "users" in prisma_cols, "prisma parse failed: users model not found"

    db_schema = await _inspect_engine(initialized_database)

    # Every Prisma column must exist in the DB (Prisma is a subset of the DB).
    for table, cols in prisma_cols.items():
        db_cols = db_schema.get(table, set())
        missing = cols - db_cols
        assert not missing, (
            f"prisma table {table}: columns missing from DB: {missing}"
        )

    # User parity: every core column exists in BOTH the backend model and the
    # Prisma model.
    backend_user_cols = _model_tables_and_columns()["users"]
    prisma_user_cols = prisma_cols["users"]
    for col in USER_CORE_COLUMNS:
        assert col in backend_user_cols, f"backend User model missing core col {col}"
        assert col in prisma_user_cols, f"prisma User model missing core col {col}"