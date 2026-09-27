-- ============================================================
-- Phase 2: Adaptive Autonomous Negotiation Sessions & Max 10 Attempts
-- ============================================================

-- 1. Create negotiation_sessions table
CREATE TABLE IF NOT EXISTS negotiation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    rfq_id UUID NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    supplier_id UUID NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'target_reached', 'supplier_final', 'awaiting_human_review', 'completed', 'expired')),
    preferred_target NUMERIC,
    acceptable_max NUMERIC,
    tolerated_final_ceiling NUMERIC,
    initial_supplier_offer NUMERIC,
    previous_supplier_offer NUMERIC,
    latest_supplier_offer NUMERIC,
    previous_agent_counter NUMERIC,
    latest_agent_counter NUMERIC,
    attempt_count INT NOT NULL DEFAULT 0,
    supplier_total_concession NUMERIC DEFAULT 0,
    supplier_last_concession NUMERIC DEFAULT 0,
    agent_total_concession NUMERIC DEFAULT 0,
    agent_last_concession NUMERIC DEFAULT 0,
    no_movement_count INT NOT NULL DEFAULT 0,
    supplier_final_detected BOOLEAN NOT NULL DEFAULT FALSE,
    selected_strategy TEXT,
    strategy_phase TEXT,
    last_quote_id UUID REFERENCES quotes(id) ON DELETE SET NULL,
    last_outbound_message_id UUID REFERENCES message_log(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

-- Unique partial index: strictly at most 1 active negotiation session per rfq_id + supplier_id
CREATE UNIQUE INDEX IF NOT EXISTS idx_negotiation_sessions_active_rfq_supp
    ON negotiation_sessions(rfq_id, supplier_id)
    WHERE status = 'active';

-- Lookup indexes
CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_client_supp_status
    ON negotiation_sessions(client_id, supplier_id, status);

CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_client_rfq_supp
    ON negotiation_sessions(client_id, rfq_id, supplier_id);

CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_updated_at
    ON negotiation_sessions(updated_at DESC);

-- RLS for negotiation_sessions
ALTER TABLE negotiation_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS negotiation_sessions_tenant_select ON negotiation_sessions;
CREATE POLICY negotiation_sessions_tenant_select ON negotiation_sessions
    FOR SELECT TO authenticated
    USING (
        client_id = public.get_auth_user_client_id()
    );

DROP POLICY IF EXISTS negotiation_sessions_anon_deny ON negotiation_sessions;
CREATE POLICY negotiation_sessions_anon_deny ON negotiation_sessions
    FOR ALL TO anon
    USING (false) WITH CHECK (false);

-- 2. Update increment_negotiation_attempts RPC to default to 10
CREATE OR REPLACE FUNCTION increment_negotiation_attempts(
    p_rfq_id UUID,
    p_supplier_id UUID,
    p_max_attempts INT DEFAULT 10
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
