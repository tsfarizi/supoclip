-- Migration: Add watermark columns to tasks table
-- This migration can be run on existing databases

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS watermark TEXT;
ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS watermark_persist BOOLEAN NOT NULL DEFAULT false;
