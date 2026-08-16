-- Schema v4: burned-in @watermark on tasks
-- watermark TEXT (nullable)
-- watermark_persist BOOLEAN NOT NULL DEFAULT false
-- a persisted watermark stays on screen for the WHOLE clip instead of the
-- fixed hook window (mirror of the hook_persist Schema v3 pattern)
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS watermark TEXT;
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS watermark_persist BOOLEAN NOT NULL DEFAULT false;
