import os

from dotenv import load_dotenv
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Load environment variables
load_dotenv()

DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://localhost:5432/supoclip"
)

_database_url_override: str | None = None
_engine_override: AsyncEngine | None = None
_session_maker_override: async_sessionmaker[AsyncSession] | None = None
_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


# Base class for all models
class Base(DeclarativeBase):
    pass


def _normalize_async_database_url(database_url: str) -> str:
    """Force the asyncpg driver for async engines.

    A plain ``postgresql://``/``postgres://`` URL makes SQLAlchemy import the
    sync psycopg2 dialect, which is not installed (and never should be for an
    async engine). An inherited DATABASE_URL (e.g. leftover from run.ps1's
    Prisma step, which strips ``+asyncpg``) silently overrides the root .env,
    so normalize at the engine boundary instead of trusting the caller.
    """
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://") :]
    return database_url


def _build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        _normalize_async_database_url(database_url),
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


def get_database_url() -> str:
    return _database_url_override or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def configure_database(
    *,
    database_url: str | None = None,
    engine: AsyncEngine | None = None,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    global _database_url_override, _engine_override, _session_maker_override
    _database_url_override = database_url
    _engine_override = engine
    _session_maker_override = session_maker


def get_engine() -> AsyncEngine:
    global _engine
    if _engine_override is not None:
        return _engine_override
    if _engine is None:
        _engine = _build_engine(get_database_url())
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker_override is not None:
        return _session_maker_override
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_maker


def AsyncSessionLocal() -> AsyncSession:
    return get_session_maker()()


# Dependency to get database session
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            # Never return a session to the pool with an open transaction:
            # a handler failure can leave one aborted. Roll back first; close
            # then releases the connection (no-op when the session is clean).
            try:
                await session.rollback()
            except Exception:
                pass
            await session.close()


# Initialize database
async def init_db():
    """Bring the schema to head via Alembic migrations (async).

    Alembic is the single migration authority; the legacy SQL runner and the
    schema_migrations ledger are no longer used. The baseline revision is a
    guarded schema snapshot, so `upgrade head` is a no-op on databases already
    migrated by the old runner (data untouched) and still converges fresh or
    partially-migrated databases to the same schema.
    """
    from src.migrations.alembic.env import run_upgrade_head

    await run_upgrade_head()


# Close database connections
async def close_db():
    global _engine, _session_maker
    engine = _engine_override or _engine
    if engine is not None:
        await engine.dispose()
    _engine = None
    _session_maker = None


async def reset_database_state() -> None:
    global _database_url_override, _engine_override, _session_maker_override
    await close_db()
    _database_url_override = None
    _engine_override = None
    _session_maker_override = None
