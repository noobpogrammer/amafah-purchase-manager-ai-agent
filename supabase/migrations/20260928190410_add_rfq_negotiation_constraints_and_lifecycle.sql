-- ============================================================
-- Phase 2.2: Negotiation Lifecycle, Authority & Constraints
-- ============================================================

-- 1. Update negotiation_sessions status constraint to support 'awaiting_authorization'
ALTER TABLE negotiation_sessions
    DROP CONSTRAINT IF EXISTS negotiation_sessions_status_check;

ALTER TABLE negotiation_sessions
    ADD CONSTRAINT negotiation_sessions_status_check
    CHECK (status IN ('active', 'target_reached', 'supplier_final', 'awaiting_human_review', 'awaiting_authorization', 'completed', 'expired'));

-- 2. Add metadata column and relax/expand category check on flagged_for_review
ALTER TABLE flagged_for_review
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

ALTER TABLE flagged_for_review
    DROP CONSTRAINT IF EXISTS flagged_for_review_category_check;

-- 3. Create rfq_negotiation_constraints table
CREATE TABLE IF NOT EXISTS rfq_negotiation_constraints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    rfq_id UUID NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL CHECK (dimension IN ('price', 'quantity', 'delivery', 'specification')),
    status TEXT NOT NULL DEFAULT 'fixed' CHECK (status IN ('fixed', 'authorized', 'unknown')),
    constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL DEFAULT 'rfq_creation' CHECK (source IN ('rfq_creation', 'operator_review')),
    authorized_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT unique_rfq_dimension UNIQUE (rfq_id, dimension)
);

CREATE INDEX IF NOT EXISTS idx_rfq_negotiation_constraints_client_rfq
    ON rfq_negotiation_constraints(client_id, rfq_id);

-- RLS for rfq_negotiation_constraints
ALTER TABLE rfq_negotiation_constraints ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rfq_negotiation_constraints_tenant_select ON rfq_negotiation_constraints;
CREATE POLICY rfq_negotiation_constraints_tenant_select ON rfq_negotiation_constraints
    FOR SELECT TO authenticated
    USING (
        client_id = public.get_auth_user_client_id()
    );

DROP POLICY IF EXISTS rfq_negotiation_constraints_tenant_all ON rfq_negotiation_constraints;
CREATE POLICY rfq_negotiation_constraints_tenant_all ON rfq_negotiation_constraints
    FOR ALL TO authenticated
    USING (
        client_id = public.get_auth_user_client_id()
    )
    WITH CHECK (
        client_id = public.get_auth_user_client_id()
    );

DROP POLICY IF EXISTS rfq_negotiation_constraints_anon_deny ON rfq_negotiation_constraints;
CREATE POLICY rfq_negotiation_constraints_anon_deny ON rfq_negotiation_constraints
    FOR ALL TO anon
    USING (false) WITH CHECK (false);

-- 4. Expire negotiation sessions helper RPC
CREATE OR REPLACE FUNCTION expire_negotiation_sessions_for_rfq_rpc(p_rfq_id UUID)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE negotiation_sessions
    SET status = 'expired',
        completed_at = COALESCE(completed_at, now()),
        updated_at = now()
    WHERE rfq_id = p_rfq_id
      AND status IN ('active', 'awaiting_human_review', 'awaiting_authorization');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE EXECUTE ON FUNCTION expire_negotiation_sessions_for_rfq_rpc(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION expire_negotiation_sessions_for_rfq_rpc(UUID) TO authenticated, service_role;
