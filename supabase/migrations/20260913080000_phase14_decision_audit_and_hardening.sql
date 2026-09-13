-- ============================================================
-- Phase 14: Decision Auditability, Webhook Recovery & Security Hardening
-- ============================================================

-- 1. Create agent_decisions table for structured audit logging
CREATE TABLE IF NOT EXISTS agent_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    rfq_id UUID REFERENCES rfqs(id) ON DELETE SET NULL,
    supplier_id UUID REFERENCES suppliers(id) ON DELETE SET NULL,
    inbound_message_id UUID REFERENCES message_log(id) ON DELETE SET NULL,
    outbound_message_id UUID REFERENCES message_log(id) ON DELETE SET NULL,
    flag_id UUID REFERENCES flagged_for_review(id) ON DELETE SET NULL,
    origin TEXT NOT NULL CHECK (origin IN ('supplier', 'operator', 'system')),
    tool_name TEXT NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('approved', 'rejected')),
    validation_reason TEXT,
    execution_status TEXT NOT NULL DEFAULT 'pending' CHECK (execution_status IN ('pending', 'executed', 'failed', 'not_executed')),
    execution_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    executed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_decisions_rfq_id ON agent_decisions(rfq_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_client_id ON agent_decisions(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_inbound_msg ON agent_decisions(inbound_message_id);

-- Row Level Security for agent_decisions (Admin-only read access within tenant)
ALTER TABLE agent_decisions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_decisions_admin_select ON agent_decisions;
CREATE POLICY agent_decisions_admin_select ON agent_decisions
    FOR SELECT TO authenticated
    USING (
        client_id = public.get_auth_user_client_id()
        AND EXISTS (
            SELECT 1 FROM public.profiles 
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

DROP POLICY IF EXISTS agent_decisions_anon_deny ON agent_decisions;
CREATE POLICY agent_decisions_anon_deny ON agent_decisions
    FOR ALL TO anon
    USING (false) WITH CHECK (false);


-- 2. Upgrade processed_webhooks for recoverable lifecycle
ALTER TABLE processed_webhooks
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('processing', 'completed', 'failed')),
    ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS last_error TEXT,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE processed_webhooks
SET status = 'completed',
    completed_at = COALESCE(completed_at, created_at)
WHERE status IS NULL OR status = 'completed';

-- Hardened claim_webhook_message supporting recoverable retries
CREATE OR REPLACE FUNCTION claim_webhook_message(
    p_client_id UUID,
    p_message_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_existing_status TEXT;
BEGIN
    SELECT status INTO v_existing_status
    FROM processed_webhooks
    WHERE client_id = p_client_id
      AND message_id = p_message_id
    FOR UPDATE;

    IF v_existing_status IS NULL THEN
        INSERT INTO processed_webhooks (client_id, message_id, status, attempt_count, updated_at)
        VALUES (p_client_id, p_message_id, 'processing', 1, now())
        ON CONFLICT (client_id, message_id) DO NOTHING;
        RETURN FOUND;
    ELSIF v_existing_status = 'failed' THEN
        UPDATE processed_webhooks
        SET status = 'processing',
            attempt_count = attempt_count + 1,
            updated_at = now()
        WHERE client_id = p_client_id
          AND message_id = p_message_id;
        RETURN TRUE;
    ELSE
        -- 'completed' or 'processing'
        RETURN FALSE;
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_webhook_message(UUID, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_webhook_message(UUID, TEXT) TO service_role;

-- complete_webhook_message RPC
CREATE OR REPLACE FUNCTION complete_webhook_message(
    p_client_id UUID,
    p_message_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    UPDATE processed_webhooks
    SET status = 'completed',
        completed_at = now(),
        updated_at = now()
    WHERE client_id = p_client_id
      AND message_id = p_message_id;
    RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION complete_webhook_message(UUID, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_webhook_message(UUID, TEXT) TO service_role;

-- fail_webhook_message RPC
CREATE OR REPLACE FUNCTION fail_webhook_message(
    p_client_id UUID,
    p_message_id TEXT,
    p_error TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    UPDATE processed_webhooks
    SET status = 'failed',
        last_error = p_error,
        updated_at = now()
    WHERE client_id = p_client_id
      AND message_id = p_message_id;
    RETURN FOUND;
END;
$$;

REVOKE EXECUTE ON FUNCTION fail_webhook_message(UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION fail_webhook_message(UUID, TEXT, TEXT) TO service_role;


-- 3. Quote deduplication for source message retries
CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_source_msg_variant
ON quotes (rfq_id, supplier_id, source_message_id, COALESCE(variant_label, ''))
WHERE source_message_id IS NOT NULL;

-- Harden record_quote_variants with ON CONFLICT deduplication and explicit security
CREATE OR REPLACE FUNCTION record_quote_variants(
    p_rfq_id UUID,
    p_supplier_id UUID,
    p_quote_group_id UUID,
    p_raw_message TEXT,
    p_source_message_id UUID,
    p_confidence TEXT,
    p_variants JSONB
)
RETURNS SETOF quotes
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rfq_status TEXT;
    v_has_available_priced_variant BOOLEAN := FALSE;
    v_item JSONB;
    v_price NUMERIC;
    v_avail BOOLEAN;
    v_variant_label TEXT;
BEGIN
    SELECT status INTO v_rfq_status
    FROM rfqs
    WHERE id = p_rfq_id;

    IF v_rfq_status IS NULL OR v_rfq_status != 'active' THEN
        RETURN;
    END IF;

    FOR v_item IN SELECT * FROM jsonb_array_elements(p_variants)
    LOOP
        v_price := CASE 
            WHEN v_item->>'price' IS NOT NULL AND v_item->>'price' != '' 
            THEN (v_item->>'price')::NUMERIC 
            ELSE NULL 
        END;
        
        v_avail := COALESCE((v_item->>'is_available')::BOOLEAN, TRUE);
        v_variant_label := NULLIF(TRIM(v_item->>'variant_label'), '');

        IF v_avail = TRUE AND v_price IS NOT NULL AND v_price > 0 THEN
            v_has_available_priced_variant := TRUE;
        END IF;

        IF p_source_message_id IS NOT NULL THEN
            RETURN QUERY
            INSERT INTO quotes (
                rfq_id,
                supplier_id,
                quote_group_id,
                variant_label,
                price,
                delivery_time,
                quality_notes,
                raw_message,
                confidence,
                is_available,
                source_message_id
            ) VALUES (
                p_rfq_id,
                p_supplier_id,
                p_quote_group_id,
                v_variant_label,
                v_price,
                v_item->>'delivery_time',
                v_item->>'quality_notes',
                p_raw_message,
                COALESCE(p_confidence, 'high'),
                v_avail,
                p_source_message_id
            )
            ON CONFLICT (rfq_id, supplier_id, source_message_id, COALESCE(variant_label, ''))
            DO UPDATE SET
                price = EXCLUDED.price,
                delivery_time = EXCLUDED.delivery_time,
                quality_notes = EXCLUDED.quality_notes,
                is_available = EXCLUDED.is_available,
                raw_message = EXCLUDED.raw_message,
                confidence = EXCLUDED.confidence
            RETURNING *;
        ELSE
            RETURN QUERY
            INSERT INTO quotes (
                rfq_id,
                supplier_id,
                quote_group_id,
                variant_label,
                price,
                delivery_time,
                quality_notes,
                raw_message,
                confidence,
                is_available,
                source_message_id
            ) VALUES (
                p_rfq_id,
                p_supplier_id,
                p_quote_group_id,
                v_variant_label,
                v_price,
                v_item->>'delivery_time',
                v_item->>'quality_notes',
                p_raw_message,
                COALESCE(p_confidence, 'high'),
                v_avail,
                p_source_message_id
            )
            RETURNING *;
        END IF;
    END LOOP;

    IF v_has_available_priced_variant THEN
        UPDATE rfq_suppliers
        SET status = 'responded'
        WHERE rfq_id = p_rfq_id
          AND supplier_id = p_supplier_id;
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION record_quote_variants(UUID, UUID, UUID, TEXT, UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION record_quote_variants(UUID, UUID, UUID, TEXT, UUID, TEXT, JSONB) TO service_role;


-- 4. Harden claim_rfq_for_finalization
CREATE OR REPLACE FUNCTION claim_rfq_for_finalization(p_rfq_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_claimed BOOLEAN := FALSE;
BEGIN
    UPDATE rfqs
    SET status = 'closed',
        finalization_status = 'processing'
    WHERE id = p_rfq_id
      AND status = 'active'
      AND (finalization_status IS NULL OR finalization_status = 'pending')
      AND (
          (due_by IS NOT NULL AND due_by <= now())
          OR (due_by IS NULL AND created_at + (COALESCE(deadline_hours, 24) || ' hours')::interval <= now())
      );

    IF FOUND THEN
        v_claimed := TRUE;

        UPDATE rfq_suppliers
        SET status = 'no_response'
        WHERE rfq_id = p_rfq_id
          AND status IN ('sent', 'clarifying');

        UPDATE pending_clarifications
        SET status = 'abandoned'
        WHERE status = 'awaiting_reply'
          AND p_rfq_id = ANY(pending_rfq_ids);
    END IF;

    RETURN v_claimed;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_rfq_for_finalization(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_rfq_for_finalization(UUID) TO service_role;


-- 5. Harden claim_outbound_message
CREATE OR REPLACE FUNCTION claim_outbound_message(
    p_message_log_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
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

REVOKE EXECUTE ON FUNCTION claim_outbound_message(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_outbound_message(UUID) TO service_role;


-- 6. Harden increment_negotiation_attempts
CREATE OR REPLACE FUNCTION increment_negotiation_attempts(
    p_rfq_id UUID,
    p_supplier_id UUID,
    p_max_attempts INT DEFAULT 3
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_new_attempts INT;
BEGIN
    UPDATE rfq_suppliers
    SET negotiation_attempts = negotiation_attempts + 1
    WHERE rfq_id = p_rfq_id
      AND supplier_id = p_supplier_id
      AND negotiation_attempts < p_max_attempts
    RETURNING negotiation_attempts INTO v_new_attempts;

    IF v_new_attempts IS NULL THEN
        RETURN -1;
    END IF;

    RETURN v_new_attempts;
END;
$$;

REVOKE EXECUTE ON FUNCTION increment_negotiation_attempts(UUID, UUID, INT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION increment_negotiation_attempts(UUID, UUID, INT) TO service_role;
