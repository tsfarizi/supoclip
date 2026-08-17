import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
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


def _split_migration_statements(sql: str) -> list[str]:
    """Split SQL without breaking dollar-quoted PostgreSQL procedural blocks."""
    statements: list[str] = []
    start = 0
    dollar_tag: str | None = None
    index = 0
    while index < len(sql):
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue

        if sql[index] == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                tag = sql[index + 1 : end]
            else:
                tag = ""
            if end != -1 and (not tag or tag.replace("_", "").isalnum()):
                dollar_tag = sql[index : end + 1]
                index = end + 1
                continue
        if sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    statement = sql[start:].strip()
    if statement:
        statements.append(statement)
    return statements


# Base class for all models
class Base(DeclarativeBase):
    pass


def _build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
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
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        migrations_dir = Path(__file__).parent / "migrations" / "sql"
        if migrations_dir.exists():
            files = sorted([p for p in migrations_dir.glob("*.sql") if p.is_file()])
            for migration_file in files:
                version = migration_file.name
                already_applied = await conn.execute(
                    text(
                        "SELECT 1 FROM schema_migrations WHERE version = :version LIMIT 1"
                    ),
                    {"version": version},
                )
                if already_applied.scalar() is not None:
                    continue

                sql = migration_file.read_text()
                # asyncpg doesn't support multiple statements in one execute(),
                # so split on semicolons and run each statement individually
                for statement in _split_migration_statements(sql):
                    await conn.execute(text(statement))
                await conn.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": version},
                )


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
