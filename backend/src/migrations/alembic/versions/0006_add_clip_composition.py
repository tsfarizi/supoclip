"""add composition_json and composition_version to generated_clips

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-01

Adds editable composition architecture columns to generated_clips:
- composition_json: nullable TEXT storing the serialized Composition schema
- composition_version: INTEGER NOT NULL DEFAULT 1 tracking the composition format version
"""

from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS composition_json TEXT")
    )
    conn.execute(
        text(
            "ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS composition_version INTEGER DEFAULT 1 NOT NULL"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS composition_version")
    )
    conn.execute(
        text("ALTER TABLE generated_clips DROP COLUMN IF EXISTS composition_json")
    )
