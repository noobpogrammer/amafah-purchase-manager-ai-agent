-- ============================================================
-- Phase 7.2: Persistent Outbound Delivery Reliability
-- Add delivery lifecycle status and metadata to message_log
-- ============================================================

ALTER TABLE message_log
    ADD COLUMN IF NOT EXISTS status text CHECK (status IS NULL OR status IN ('queued', 'sending', 'sent', 'failed', 'unknown')),
    ADD COLUMN IF NOT EXISTS evolution_message_id text,
    ADD COLUMN IF NOT EXISTS error_message text,
    ADD COLUMN IF NOT EXISTS attempted_at timestamptz,
    ADD COLUMN IF NOT EXISTS sent_at timestamptz,
    ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0;

-- Index for efficient startup recovery scan
CREATE INDEX IF NOT EXISTS idx_message_log_queued ON message_log(status, created_at) WHERE status = 'queued';
