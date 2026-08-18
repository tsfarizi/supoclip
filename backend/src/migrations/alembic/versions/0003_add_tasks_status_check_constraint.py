"""add tasks.status CHECK constraint (DB-enforced state machine guard)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

P2: every status write now goes through the CAS method
(TaskRepository.update_task_status, WHERE id = :id AND status IN :expected).
The CHECK constraint closes the loop at the storage layer: any writer that
bypasses the CAS (a future code path, an ad-hoc SQL UPDATE, a manual row
edit) can no longer persist an unknown status value. The ADD is guarded
against an already-existing constraint so all database states converge to
head; the downgrade drops only this constraint.
"""
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_STATUS_CONSTRAINT = "chk_tasks_status"
_ALLOWED_STATUSES = (
    "'pending', 'queued', 'processing', 'completed', 'error', 'cancelled', 'deleted'"
)


def upgrade() -> None:
    op.execute(
        text(
            f"""
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{_STATUS_CONSTRAINT}'
                      AND conrelid = 'tasks'::regclass
                ) THEN
                    ALTER TABLE tasks ADD CONSTRAINT {_STATUS_CONSTRAINT}
                    CHECK (status IN ({_ALLOWED_STATUSES}));
                END IF;
            END
            $supoclip$
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(f"ALTER TABLE tasks DROP CONSTRAINT IF EXISTS {_STATUS_CONSTRAINT}")
    )