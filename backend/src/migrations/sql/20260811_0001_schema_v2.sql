-- Schema v2 (docs/schema/schema_v2.md)
-- (1) Persist task source configuration that previously lived only in Redis
-- (task_source:{task_id})
-- (2) Close the create_all gap vs init.sql (users.default_font_*, id defaults)
-- (3) Enforce sources.url NOT NULL
-- No DO blocks here: init_db() splits files on semicolons and PL/pgSQL bodies
-- always contain them, so procedural blocks cannot survive the runner.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- tasks: output_format / add_subtitles / cleanup_settings_json (audit 4.1)
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS output_format VARCHAR(20) NOT NULL DEFAULT 'vertical',
    ADD COLUMN IF NOT EXISTS add_subtitles BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS cleanup_settings_json JSONB;

-- Constraint names are fresh and the file runs once via schema_migrations,
-- so plain ADD CONSTRAINT is safe in the migration lifecycle.
ALTER TABLE tasks
    ADD CONSTRAINT chk_tasks_output_format
    CHECK (output_format IN ('vertical', 'vertical_pan', 'vertical_split', 'original'));

ALTER TABLE tasks
    ADD CONSTRAINT chk_tasks_cleanup_settings_json
    CHECK (jsonb_typeof(cleanup_settings_json) = 'object');

-- users: default font preferences, matching init.sql defaults (audit 4.2)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS default_font_family VARCHAR(100) DEFAULT 'TikTokSans-Regular',
    ADD COLUMN IF NOT EXISTS default_font_size INTEGER DEFAULT 24,
    ADD COLUMN IF NOT EXISTS default_font_color VARCHAR(7) DEFAULT '#FFFFFF';

-- Sync id server defaults with init.sql so raw INSERTs that omit id succeed
-- (root cause of the failing clip INSERT: generated_clips.id had no default)
ALTER TABLE generated_clips ALTER COLUMN id SET DEFAULT uuid_generate_v4()::text;
ALTER TABLE tasks ALTER COLUMN id SET DEFAULT uuid_generate_v4()::text;
ALTER TABLE sources ALTER COLUMN id SET DEFAULT uuid_generate_v4()::text;

-- sources.url: backfill NULLs first, then enforce NOT NULL (audit 4.3)
-- Backfill value '' matches the runtime contract COALESCE(s.url, '') used by
-- the task list queries. Re-running the UPDATE is a no-op (no NULLs remain)
-- and SET NOT NULL on an already-NOT-NULL column is a no-op.
UPDATE sources SET url = '' WHERE url IS NULL;
ALTER TABLE sources ALTER COLUMN url SET NOT NULL;
