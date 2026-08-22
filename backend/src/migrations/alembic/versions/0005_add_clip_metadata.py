"""add clip metadata columns (description, hashtags, metadata_status)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-23

Adds marketing metadata to generated_clips: per-clip AI description
and hashtag list, plus status/version tracking. The columns are nullable
except metadata_status which defaults to 'pending' for backward
compatibility. Existing rows stay pending; new clips write ready/degraded.
"""

from alembic import op
from sqlalchemy import text

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS description TEXT"))
    conn.execute(text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hashtags TEXT"))
    conn.execute(
        text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_status VARCHAR(20) DEFAULT 'pending' NOT NULL")
    )
    conn.execute(text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_version VARCHAR(20)"))
    conn.execute(
        text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_prompt_version VARCHAR(40)")
    )
    conn.execute(
        text(
            """
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'chk_generated_clips_metadata_status'
                      AND conrelid = 'generated_clips'::regclass
                ) THEN
                    ALTER TABLE generated_clips ADD CONSTRAINT chk_generated_clips_metadata_status
                    CHECK (metadata_status IN ('pending','ready','degraded','failed'));
                END IF;
            END
            $supoclip$
            """
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS idx_generated_clips_metadata_status ON generated_clips(metadata_status)")
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP INDEX IF EXISTS idx_generated_clips_metadata_status"))
    conn.execute(text("ALTER TABLE generated_clips DROP CONSTRAINT IF EXISTS chk_generated_clips_metadata_status"))
    conn.execute(text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS metadata_prompt_version"))
    conn.execute(text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS metadata_version"))
    conn.execute(text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS metadata_status"))
    conn.execute(text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS hashtags"))
    conn.execute(text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS description"))
