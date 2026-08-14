-- Schema v3: hook title persistence flag
-- hook_persist BOOLEAN NOT NULL DEFAULT false on tasks
-- a persisted hook stays on screen for the WHOLE clip instead of the fixed intro window
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS hook_persist BOOLEAN NOT NULL DEFAULT false;
