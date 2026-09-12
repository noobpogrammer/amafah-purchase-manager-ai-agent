-- ============================================================
-- Phase 7.1: Durable WhatsApp Webhook Idempotency
-- Creates processed_webhooks table for tenant-scoped message deduplication
-- ============================================================

CREATE TABLE IF NOT EXISTS processed_webhooks (
    client_id   UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    message_id  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_processed_webhooks_created_at ON processed_webhooks(created_at);

-- Enable Row Level Security
ALTER TABLE processed_webhooks ENABLE ROW LEVEL SECURITY;

-- Deny public anon access; service-role backend has full access
DROP POLICY IF EXISTS anon_processed_webhooks_deny ON processed_webhooks;
CREATE POLICY anon_processed_webhooks_deny ON processed_webhooks FOR ALL TO anon USING (false) WITH CHECK (false);

-- Atomic claim function using ON CONFLICT DO NOTHING
CREATE OR REPLACE FUNCTION claim_webhook_message(
    p_client_id UUID,
    p_message_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted_id TEXT;
BEGIN
    INSERT INTO processed_webhooks (client_id, message_id)
    VALUES (p_client_id, p_message_id)
    ON CONFLICT (client_id, message_id) DO NOTHING
    RETURNING message_id INTO v_inserted_id;

    RETURN v_inserted_id IS NOT NULL;
END;
$$;
