-- Migration: Add hook_persist column to tasks table
-- This migration can be run on existing databases

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS hook_persist BOOLEAN NOT NULL DEFAULT false;
