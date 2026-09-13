-- Phase 12: Semantic Clarification Loop Control Schema
-- Adds no_progress_count and last_question to pending_clarifications

ALTER TABLE pending_clarifications
ADD COLUMN IF NOT EXISTS no_progress_count INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_question TEXT;
