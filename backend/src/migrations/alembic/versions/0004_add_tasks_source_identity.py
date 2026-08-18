"""add tasks.source_identity + partial unique index for duplicate-submission guard

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17

P4: the duplicate-submission race is closed at the storage layer. A new
nullable ``tasks.source_identity`` column holds the canonical identity
(``youtube:<video_id>`` for YouTube, verbatim URL for uploads) computed by the
same Python function the create path uses
(``src.services.task_service.normalize_video_identity``). A partial unique
index ``uq_tasks_source_identity_active`` enforces one in-flight task per
identity: two parallel submissions of the same video resolve to exactly one
winner at INSERT time, and the loser's transaction rolls back
(``DuplicateTaskError`` at the service layer).

The column is backfilled for non-terminal rows from ``sources.url`` using the
Python normalizer, so existing in-flight rows participate in the guarantee
immediately. Rows whose URL is empty become NULL (the partial index skips
NULLs, so no constraint is violated and the column stays honest).

If duplicate identities exist among non-terminal rows, the unique index
creation fails and the migration aborts loudly: the operator must resolve the
duplicates. No silent data loss.
"""
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_COLUMN = "source_identity"
_INDEX = "uq_tasks_source_identity_active"
_TERMINAL_STATUSES = ("'completed', 'error', 'cancelled'")


def _guard_add_column(conn) -> None:
    conn.execute(
        text(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {_COLUMN} VARCHAR(255)")
    )


def _backfill_source_identity(conn) -> None:
    # Imported inside the migration so the normalizer lives in one place and
    # the migration and the create path can never drift apart.
    from src.services.task_service import normalize_video_identity

    rows = conn.execute(
        text(
            f"""
            SELECT t.id, COALESCE(s.url, '') AS url
            FROM tasks t
            LEFT JOIN sources s ON s.id = t.source_id
            WHERE t.status NOT IN ({_TERMINAL_STATUSES})
              AND t.{_COLUMN} IS NULL
            ORDER BY t.created_at ASC
            """
        )
    ).fetchall()

    for row in rows:
        identity = normalize_video_identity(row.url or "")
        # Empty URL -> NULL, never an empty string: the partial index skips
        # NULL so upload-less legacy rows do not collide on ''.
        value = identity or None
        conn.execute(
            text(
                f"UPDATE tasks SET {_COLUMN} = :identity WHERE id = :id"
            ),
            {"identity": value, "id": row.id},
        )


def upgrade() -> None:
    conn = op.get_bind()
    _guard_add_column(conn)
    _backfill_source_identity(conn)
    # Fail loud on duplicates: CREATE UNIQUE INDEX raises on any duplicate
    # identity among non-terminal rows instead of silently dropping data.
    conn.execute(
        text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX}
            ON tasks({_COLUMN})
            WHERE status NOT IN ({_TERMINAL_STATUSES})
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"DROP INDEX IF EXISTS {_INDEX}"))
    conn.execute(text(f"ALTER TABLE tasks DROP COLUMN IF EXISTS {_COLUMN}"))