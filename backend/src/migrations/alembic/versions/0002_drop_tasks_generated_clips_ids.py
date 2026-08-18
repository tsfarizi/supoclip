"""drop tasks.generated_clips_ids (single source of truth for clips)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

P1: the `generated_clips_ids` ARRAY column on tasks is a denormalized
duplicate of `generated_clips` that was only maintained on the create path
(`TaskRepository.update_task_clips`) and never on delete/split/merge/
regenerate. The column is removed; reads derive clip membership exclusively
from `generated_clips`. The DROP is existence-guarded so a database that
already lost the column (partial manual cleanup) still converges to head.
"""
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            """
            DO $supoclip$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'tasks'
                      AND column_name = 'generated_clips_ids'
                ) THEN
                    ALTER TABLE tasks DROP COLUMN generated_clips_ids;
                END IF;
            END
            $supoclip$
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            """
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'tasks'
                      AND column_name = 'generated_clips_ids'
                ) THEN
                    ALTER TABLE tasks ADD COLUMN generated_clips_ids VARCHAR(36)[];
                END IF;
            END
            $supoclip$
            """
        )
    )