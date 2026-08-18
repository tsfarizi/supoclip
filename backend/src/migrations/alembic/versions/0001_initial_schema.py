"""baseline: full SupoClip schema snapshot (init.sql + cumulative migrations)

Revision ID: 0001
Revises:
Create Date: 2026-08-17

Baseline approach -- guarded schema snapshot instead of an empty marker:
an empty baseline would leave fresh databases short of the migrated state
because init.sql predates Schema v2 (tasks.output_format / add_subtitles /
cleanup_settings_json, sources.url NOT NULL). Every DDL below is guarded
(IF NOT EXISTS, or existence checks inside DO blocks) so one revision
converges all three database states to the same head schema:
  - legacy-migrated databases (full schema present): pure no-op,
  - init.sql-bootstrapped databases: fills only the post-init.sql gap,
  - empty databases: creates the complete schema.
No destructive DDL exists anywhere in this revision.
"""
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    _create_tables()
    _migration_gap()
    _schema_v2_constraints()
    _indexes()
    _functions()
    _triggers()


def _create_tables() -> None:
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                "emailVerified" BOOLEAN NOT NULL DEFAULT false,
                image VARCHAR(500),
                "createdAt" TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                "updatedAt" TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                password_hash VARCHAR(255),
                default_font_family VARCHAR(100) DEFAULT 'TikTokSans-Regular',
                default_font_size INTEGER DEFAULT 24,
                default_font_color VARCHAR(7) DEFAULT '#FFFFFF',
                notify_on_completion BOOLEAN NOT NULL DEFAULT true,
                is_admin BOOLEAN NOT NULL DEFAULT false,
                plan VARCHAR(20) NOT NULL DEFAULT 'free',
                subscription_status VARCHAR(20) NOT NULL DEFAULT 'inactive',
                subscription_provider VARCHAR(20),
                stripe_customer_id VARCHAR(255) UNIQUE,
                stripe_subscription_id VARCHAR(255) UNIQUE,
                billing_period_start TIMESTAMP WITH TIME ZONE,
                billing_period_end TIMESTAMP WITH TIME ZONE,
                trial_ends_at TIMESTAMP WITH TIME ZONE
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
                type VARCHAR(20) CHECK (type IN ('youtube', 'video_url')) NOT NULL,
                title VARCHAR(500) NOT NULL,
                url VARCHAR(1000),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source_id VARCHAR(36) REFERENCES sources(id) ON DELETE SET NULL,
                generated_clips_ids VARCHAR(36)[],
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
                progress_message TEXT,
                font_family VARCHAR(100) DEFAULT 'TikTokSans-Regular',
                font_size INTEGER DEFAULT 24,
                font_color VARCHAR(7) DEFAULT '#FFFFFF',
                caption_template VARCHAR(50) DEFAULT 'default',
                include_broll BOOLEAN DEFAULT false,
                sound_effects_count INTEGER NOT NULL DEFAULT 0
                    CHECK (sound_effects_count >= 0 AND sound_effects_count <= 5),
                hook_persist BOOLEAN NOT NULL DEFAULT false,
                watermark TEXT,
                watermark_persist BOOLEAN NOT NULL DEFAULT false,
                processing_mode VARCHAR(20) NOT NULL DEFAULT 'fast',
                started_at TIMESTAMP WITH TIME ZONE,
                completed_at TIMESTAMP WITH TIME ZONE,
                cache_hit BOOLEAN NOT NULL DEFAULT false,
                error_code VARCHAR(80),
                stage_timings_json TEXT,
                completion_notification_sent_at TIMESTAMP WITH TIME ZONE,
                share_token VARCHAR(64),
                share_enabled BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS generated_clips (
                id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
                task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                filename VARCHAR(255) NOT NULL,
                file_path VARCHAR(500) NOT NULL,
                start_time VARCHAR(20) NOT NULL,
                end_time VARCHAR(20) NOT NULL,
                duration FLOAT NOT NULL,
                text TEXT,
                relevance_score FLOAT NOT NULL,
                reasoning TEXT,
                clip_order INTEGER NOT NULL,
                virality_score INTEGER DEFAULT 0,
                hook_score INTEGER DEFAULT 0,
                engagement_score INTEGER DEFAULT 0,
                value_score INTEGER DEFAULT 0,
                shareability_score INTEGER DEFAULT 0,
                hook_type VARCHAR(50),
                hook_title VARCHAR(200),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS processing_cache (
                cache_key VARCHAR(255) PRIMARY KEY,
                source_url TEXT NOT NULL,
                source_type VARCHAR(20) NOT NULL,
                video_path TEXT,
                transcript_text TEXT,
                analysis_json TEXT,
                sound_effects_count INTEGER NOT NULL DEFAULT 0
                    CHECK (sound_effects_count >= 0 AND sound_effects_count <= 5),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS session (
                id VARCHAR(36) PRIMARY KEY,
                "expiresAt" TIMESTAMP WITH TIME ZONE NOT NULL,
                token VARCHAR(255) UNIQUE NOT NULL,
                "createdAt" TIMESTAMP WITH TIME ZONE NOT NULL,
                "updatedAt" TIMESTAMP WITH TIME ZONE NOT NULL,
                "ipAddress" VARCHAR(255),
                "userAgent" TEXT,
                "userId" VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS account (
                id VARCHAR(36) PRIMARY KEY,
                "accountId" VARCHAR(255) NOT NULL,
                "providerId" VARCHAR(255) NOT NULL,
                "userId" VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                "accessToken" TEXT,
                "refreshToken" TEXT,
                "idToken" TEXT,
                "accessTokenExpiresAt" TIMESTAMP WITH TIME ZONE,
                "refreshTokenExpiresAt" TIMESTAMP WITH TIME ZONE,
                scope TEXT,
                password TEXT,
                "createdAt" TIMESTAMP WITH TIME ZONE NOT NULL,
                "updatedAt" TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS verification (
                id VARCHAR(36) PRIMARY KEY,
                identifier VARCHAR(255) NOT NULL,
                value VARCHAR(255) NOT NULL,
                "expiresAt" TIMESTAMP WITH TIME ZONE NOT NULL,
                "createdAt" TIMESTAMP WITH TIME ZONE,
                "updatedAt" TIMESTAMP WITH TIME ZONE
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                id VARCHAR(255) PRIMARY KEY,
                type VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS revenuecat_webhook_events (
                id VARCHAR(255) PRIMARY KEY,
                type VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key VARCHAR(100) PRIMARY KEY,
                encrypted_value TEXT NOT NULL,
                prefer_admin_value BOOLEAN NOT NULL DEFAULT false,
                updated_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(120) NOT NULL DEFAULT 'API Key',
                key_hash VARCHAR(64) NOT NULL UNIQUE,
                key_prefix VARCHAR(16) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP WITH TIME ZONE,
                revoked_at TIMESTAMP WITH TIME ZONE
            )
            """
        )
    )


def _migration_gap() -> None:
    """Additive, guarded statements that replicate every legacy SQL migration
    (20260302_0001_performance_schema.sql .. 20260816_0001_watermark.sql)."""
    # tasks: performance schema
    op.execute(
        text(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS processing_mode VARCHAR(20) NOT NULL DEFAULT 'fast'"
        )
    )
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE")
    )
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE")
    )
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN NOT NULL DEFAULT FALSE")
    )
    op.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS error_code VARCHAR(80)"))
    op.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS stage_timings_json TEXT"))
    # tasks: processing columns
    op.execute(
        text(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress INTEGER DEFAULT 0 "
            "CHECK (progress >= 0 AND progress <= 100)"
        )
    )
    op.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS progress_message TEXT"))
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS caption_template VARCHAR(50) DEFAULT 'default'")
    )
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS include_broll BOOLEAN DEFAULT FALSE")
    )
    op.execute(text("ALTER TABLE sources ADD COLUMN IF NOT EXISTS url VARCHAR(1000)"))
    # tasks: share tokens
    op.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS share_token VARCHAR(64)"))
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS share_enabled BOOLEAN NOT NULL DEFAULT false")
    )
    # tasks: Schema v2 (output_format / add_subtitles / cleanup_settings_json)
    op.execute(
        text(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS output_format VARCHAR(20) NOT NULL DEFAULT 'vertical'"
        )
    )
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS add_subtitles BOOLEAN NOT NULL DEFAULT TRUE")
    )
    op.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cleanup_settings_json JSONB"))
    # tasks: Schema v3 hook persist + Schema v4 watermark
    op.execute(
        text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS hook_persist BOOLEAN NOT NULL DEFAULT false")
    )
    op.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS watermark TEXT"))
    op.execute(
        text(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS watermark_persist BOOLEAN NOT NULL DEFAULT false"
        )
    )
    # users: default font preferences (Schema v2 create_all gap)
    op.execute(
        text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS default_font_family VARCHAR(100) "
            "DEFAULT 'TikTokSans-Regular'"
        )
    )
    op.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS default_font_size INTEGER DEFAULT 24"))
    op.execute(
        text("ALTER TABLE users ADD COLUMN IF NOT EXISTS default_font_color VARCHAR(7) DEFAULT '#FFFFFF'")
    )
    # app_settings: preference flag (migration 20260503_0001/0002)
    op.execute(
        text(
            "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS prefer_admin_value BOOLEAN NOT NULL DEFAULT false"
        )
    )
    # generated_clips: hook title (migration 20260704)
    op.execute(text("ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_title VARCHAR(200)"))
    # processing_cache: sound_effects_count for DBs whose table predates init.sql
    op.execute(
        text(
            "ALTER TABLE processing_cache ADD COLUMN IF NOT EXISTS sound_effects_count INTEGER "
            "NOT NULL DEFAULT 0 CHECK (sound_effects_count >= 0 AND sound_effects_count <= 5)"
        )
    )
    # id server defaults so raw INSERTs that omit id succeed (Schema v2)
    op.execute(
        text(
            "ALTER TABLE generated_clips ALTER COLUMN id SET DEFAULT uuid_generate_v4()::text"
        )
    )
    op.execute(text("ALTER TABLE tasks ALTER COLUMN id SET DEFAULT uuid_generate_v4()::text"))
    op.execute(text("ALTER TABLE sources ALTER COLUMN id SET DEFAULT uuid_generate_v4()::text"))
    # sources.url: backfill NULLs first, then enforce NOT NULL (Schema v2).
    # Re-running is a no-op (no NULLs remain) and SET NOT NULL on an already
    # NOT NULL column is a no-op.
    op.execute(text("UPDATE sources SET url = '' WHERE url IS NULL"))
    op.execute(text("ALTER TABLE sources ALTER COLUMN url SET NOT NULL"))


def _schema_v2_constraints() -> None:
    op.execute(
        text(
            """
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'chk_tasks_output_format'
                      AND conrelid = 'tasks'::regclass
                ) THEN
                    ALTER TABLE tasks ADD CONSTRAINT chk_tasks_output_format
                    CHECK (output_format IN ('vertical', 'vertical_pan', 'vertical_split', 'original'));
                END IF;
            END
            $supoclip$
            """
        )
    )
    op.execute(
        text(
            """
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'chk_tasks_cleanup_settings_json'
                      AND conrelid = 'tasks'::regclass
                ) THEN
                    ALTER TABLE tasks ADD CONSTRAINT chk_tasks_cleanup_settings_json
                    CHECK (jsonb_typeof(cleanup_settings_json) = 'object');
                END IF;
            END
            $supoclip$
            """
        )
    )


def _indexes() -> None:
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_source_id ON tasks(source_id)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_processing_mode ON tasks(processing_mode)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_completed_at ON tasks(completed_at)"))
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_share_token "
            "ON tasks(share_token) WHERE share_token IS NOT NULL"
        )
    )
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_sources_created_at ON sources(created_at)"))
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_processing_cache_source_url ON processing_cache(source_url)"
        )
    )
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_generated_clips_task_id ON generated_clips(task_id)"))
    op.execute(
        text("CREATE INDEX IF NOT EXISTS idx_generated_clips_clip_order ON generated_clips(clip_order)")
    )
    op.execute(
        text("CREATE INDEX IF NOT EXISTS idx_generated_clips_created_at ON generated_clips(created_at)")
    )
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_session_token ON session(token)"))
    op.execute(text('CREATE INDEX IF NOT EXISTS idx_session_userId ON session("userId")'))
    op.execute(text('CREATE INDEX IF NOT EXISTS idx_account_userId ON account("userId")'))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_verification_identifier ON verification(identifier)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_app_settings_updated_by ON app_settings(updated_by)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id)"))
    op.execute(text("CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash)"))


def _functions() -> None:
    # Existence-guarded (not CREATE OR REPLACE): init.sql installs these as the
    # postgres role on fresh/dev databases, and REPLACE requires ownership, so
    # only a missing function may be created (by the migrating role). Because
    # the baseline runs once per database, a plain guard is sufficient.
    _create_function_if_missing(
        "update_updated_at_column",
        """
        CREATE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $fnbody$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $fnbody$ language 'plpgsql'
        """,
    )
    _create_function_if_missing(
        "update_updatedat_column",
        """
        CREATE FUNCTION update_updatedAt_column()
        RETURNS TRIGGER AS $fnbody$
        BEGIN
            NEW."updatedAt" = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $fnbody$ language 'plpgsql'
        """,
    )


def _create_function_if_missing(proname: str, create_sql: str) -> None:
    op.execute(
        text(
            f"""
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_proc
                    WHERE pronamespace = 'public'::regnamespace
                      AND proname = '{proname}'
                ) THEN
                    {create_sql};
                END IF;
            END
            $supoclip$
            """
        )
    )


def _triggers() -> None:
    # Postgres folds unquoted trigger names to lowercase, so the guards match
    # the stored pg_trigger.tgname values.
    _create_trigger_if_missing("update_users_updatedat", "users", "update_updatedAt_column()")
    _create_trigger_if_missing("update_tasks_updated_at", "tasks", "update_updated_at_column()")
    _create_trigger_if_missing("update_sources_updated_at", "sources", "update_updated_at_column()")
    _create_trigger_if_missing(
        "update_generated_clips_updated_at", "generated_clips", "update_updated_at_column()"
    )
    _create_trigger_if_missing(
        "update_app_settings_updated_at", "app_settings", "update_updated_at_column()"
    )
    _create_trigger_if_missing("update_session_updatedat", "session", "update_updatedAt_column()")
    _create_trigger_if_missing("update_account_updatedat", "account", "update_updatedAt_column()")
    _create_trigger_if_missing(
        "update_verification_updatedat", "verification", "update_updatedAt_column()"
    )


def _create_trigger_if_missing(tgname: str, table: str, function: str) -> None:
    op.execute(
        text(
            f"""
            DO $supoclip$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = '{tgname}' AND NOT tgisinternal
                ) THEN
                    CREATE TRIGGER {tgname} BEFORE UPDATE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION {function};
                END IF;
            END
            $supoclip$
            """
        )
    )


def downgrade() -> None:
    # Baseline: nothing to roll back. Dropping the schema would be destructive
    # and this revision exists only to pin the pre-Alembic state as head.
    pass