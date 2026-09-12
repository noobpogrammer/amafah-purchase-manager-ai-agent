-- ============================================================
-- Phase 9 Final Hardening: Operator Review Flag Lifecycle RPCs
-- Hardens claim_flag_for_operator_action, release_flag_claim, and complete_flag_operator_action
-- Enforces SECURITY DEFINER, SET search_path = public, and service_role ONLY access
-- ============================================================

-- 1. Hardened claim_flag_for_operator_action (pending -> processing)
CREATE OR REPLACE FUNCTION claim_flag_for_operator_action(
    p_flag_id UUID,
    p_client_id UUID
)
RETURNS SETOF flagged_for_review
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    UPDATE flagged_for_review
    SET status = 'processing'
    WHERE id = p_flag_id
      AND client_id = p_client_id
      AND status = 'pending'
    RETURNING *;
END;
$$;

REVOKE EXECUTE ON FUNCTION claim_flag_for_operator_action(UUID, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_flag_for_operator_action(UUID, UUID) TO service_role;

-- 2. Hardened release_flag_claim (processing -> pending)
CREATE OR REPLACE FUNCTION release_flag_claim(
    p_flag_id UUID,
    p_client_id UUID
)
RETURNS SETOF flagged_for_review
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    UPDATE flagged_for_review
    SET status = 'pending'
    WHERE id = p_flag_id
      AND client_id = p_client_id
      AND status = 'processing'
    RETURNING *;
END;
$$;

REVOKE EXECUTE ON FUNCTION release_flag_claim(UUID, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION release_flag_claim(UUID, UUID) TO service_role;

-- 3. Hardened complete_flag_operator_action (processing -> resolved)
CREATE OR REPLACE FUNCTION complete_flag_operator_action(
    p_flag_id UUID,
    p_client_id UUID,
    p_human_response TEXT
)
RETURNS SETOF flagged_for_review
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    UPDATE flagged_for_review
    SET status = 'resolved',
        human_response = p_human_response,
        resolved_at = NOW()
    WHERE id = p_flag_id
      AND client_id = p_client_id
      AND status = 'processing'
    RETURNING *;
END;
$$;

REVOKE EXECUTE ON FUNCTION complete_flag_operator_action(UUID, UUID, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_flag_operator_action(UUID, UUID, TEXT) TO service_role;
