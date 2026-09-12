-- ============================================================
-- Phase 7.2: Atomic Outbound Send Acquisition RPC
-- Atomically claims a queued message_log row for sending
-- ============================================================

CREATE OR REPLACE FUNCTION claim_outbound_message(
    p_message_log_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_claimed_id UUID;
BEGIN
    UPDATE message_log
    SET
        status = 'sending',
        attempted_at = now(),
        retry_count = retry_count + 1
    WHERE id = p_message_log_id
      AND direction = 'outbound'
      AND status = 'queued'
    RETURNING id INTO v_claimed_id;

    RETURN v_claimed_id IS NOT NULL;
END;
$$;
