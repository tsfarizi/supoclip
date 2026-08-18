"""Alembic environment for the SupoClip backend (async engine).

Baseline approach -- guarded schema snapshot instead of an empty marker:
An empty baseline revision would be a no-op on already-migrated databases but
would leave fresh databases short of the migrated state: init.sql predates
Schema v2 (tasks.output_format / add_subtitles / cleanup_settings_json and
sources.url NOT NULL arrive via SQL migrations), so "init.sql + stamp" would
not converge to the schema the legacy runner produced. The baseline is
therefore a full schema snapshot where every DDL is guarded (IF NOT EXISTS or
existence checks inside DO blocks): on an empty database it creates the whole
schema, on an init.sql-fresh database it only fills the migration gap, and on
a legacy-migrated database it is a pure no-op. No destructive DDL exists.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Importing the models module registers every declarative table on
# Base.metadata; target_metadata points at that metadata.
from src import models  # noqa: F401,E402
from src.database import Base, get_database_url, get_engine  # noqa: E402

target_metadata = Base.metadata

# The CLI installs an EnvironmentContext proxy before executing this file, so
# `context.config` only exists during CLI-driven runs. A plain import from
# src.database.init_db must not auto-run migrations; that path drives
# `do_run_migrations` itself inside a proxy context.
if hasattr(context, "config") and context.config is not None:
    config = context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)


def _script_directory() -> ScriptDirectory:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent))
    return ScriptDirectory.from_config(cfg)


def run_migrations_offline() -> None:
    """Generate SQL offline (e.g. `alembic upgrade head --sql`)."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_programmatic_upgrade(connection: Connection) -> None:
    """Drive an `upgrade head` inside the caller's sync-wrapped connection.

    Mirrors alembic.command.upgrade but reuses the connection already opened
    by the async engine, so no asyncio.run() is spawned inside the running
    event loop of the application. Only used when no EnvironmentContext proxy
    is already installed (i.e. src.database.init_db).
    """
    script = _script_directory()

    def upgrade(rev, context):
        return script._upgrade_revs("head", rev)

    with EnvironmentContext(
        Config(),
        script,
        fn=upgrade,
        as_sql=False,
        starting_rev=None,
        destination_rev="head",
    ):
        do_run_migrations(connection)


async def run_upgrade_head() -> None:
    """Programmatic async entrypoint used by src.database.init_db()."""
    engine = get_engine()
    async with engine.connect() as connection:
        if hasattr(context, "config") and context.config is not None:
            # CLI-driven: the command already installed an EnvironmentContext
            # proxy; reuse it so its teardown stays symmetric. Installing a
            # second one here would make the CLI's __exit__ double-delete.
            await connection.run_sync(do_run_migrations)
        else:
            await connection.run_sync(_run_programmatic_upgrade)


def run_migrations_online() -> None:
    """CLI path (e.g. `alembic upgrade head` invoked by run.ps1)."""
    asyncio.run(run_upgrade_head())


# CLI dispatch is deferred to the end of the module: the functions it invokes
# are defined above and the plain-import guard still applies.
if hasattr(context, "config") and context.config is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()