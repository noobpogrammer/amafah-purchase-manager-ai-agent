-- ============================================================
-- Phase 2.2 Hardening: required_delivery_days, Atomic RFQ RPC & RPC Security
-- ============================================================

-- 1. Add required_delivery_days column to rfqs table
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS required_delivery_days INTEGER;

-- 2. Harden expire_negotiation_sessions_for_rfq_rpc permissions (service_role only)
REVOKE EXECUTE ON FUNCTION expire_negotiation_sessions_for_rfq_rpc(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION expire_negotiation_sessions_for_rfq_rpc(UUID) TO service_role;

-- 3. Create atomic RFQ + Constraints Creation RPC
CREATE OR REPLACE FUNCTION create_rfq_with_constraints_rpc(
    p_client_id UUID,
    p_product_name TEXT,
    p_category TEXT,
    p_specs TEXT,
    p_quantity INT,
    p_last_quote NUMERIC,
    p_acceptable_price_min NUMERIC,
    p_acceptable_price_max NUMERIC,
    p_deadline_hours INT,
    p_required_delivery_days INT,
    p_constraints JSONB DEFAULT '[]'::jsonb
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rfq_id UUID;
    v_rfq_row RECORD;
    v_due_by TIMESTAMPTZ;
    v_elem JSONB;
BEGIN
    v_due_by := now() + (COALESCE(p_deadline_hours, 24) || ' hours')::interval;

    INSERT INTO rfqs (
        client_id,
        product_name,
        category,
        specs,
        quantity,
        last_quote,
        acceptable_price_min,
        acceptable_price_max,
        deadline_hours,
        required_delivery_days,
        status,
        due_by
    ) VALUES (
        p_client_id,
        p_product_name,
        p_category,
        p_specs,
        p_quantity,
        p_last_quote,
        p_acceptable_price_min,
        p_acceptable_price_max,
        COALESCE(p_deadline_hours, 24),
        p_required_delivery_days,
        'active',
        v_due_by
    ) RETURNING * INTO v_rfq_row;

    v_rfq_id := v_rfq_row.id;

    IF p_constraints IS NOT NULL AND jsonb_array_length(p_constraints) > 0 THEN
        FOR v_elem IN SELECT * FROM jsonb_array_elements(p_constraints)
        LOOP
            INSERT INTO rfq_negotiation_constraints (
                client_id,
                rfq_id,
                dimension,
                status,
                constraints,
                source,
                authorized_by
            ) VALUES (
                p_client_id,
                v_rfq_id,
                v_elem->>'dimension',
                COALESCE(v_elem->>'status', 'fixed'),
                COALESCE(v_elem->'constraints', '{}'::jsonb),
                COALESCE(v_elem->>'source', 'rfq_creation'),
                CASE WHEN (v_elem->>'authorized_by') IS NOT NULL AND (v_elem->>'authorized_by') != ''
                     THEN (v_elem->>'authorized_by')::UUID
                     ELSE NULL END
            );
        END LOOP;
    END IF;

    RETURN to_jsonb(v_rfq_row);
END;
$$;

REVOKE EXECUTE ON FUNCTION create_rfq_with_constraints_rpc FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION create_rfq_with_constraints_rpc TO authenticated, service_role;
