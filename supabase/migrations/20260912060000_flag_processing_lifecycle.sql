-- ============================================================
-- Phase 9: Operator Review Flag Claim & Processing Lifecycle
-- Expands flagged_for_review.status CHECK constraint to include 'processing'
-- Adds atomic claim_flag_for_operator_action RPC
-- ============================================================

-- 1. Update status check constraint on flagged_for_review
ALTER TABLE flagged_for_review 
    DROP CONSTRAINT IF EXISTS flagged_for_review_status_check;

ALTER TABLE flagged_for_review 
    ADD CONSTRAINT flagged_for_review_status_check 
    CHECK (status IN ('pending', 'processing', 'resolved', 'dismissed'));

-- 2. Atomic claim function for operator responses
CREATE OR REPLACE FUNCTION claim_flag_for_operator_action(
    p_flag_id UUID,
    p_client_id UUID
)
RETURNS SETOF flagged_for_review
LANGUAGE sql
SECURITY DEFINER
AS $$
    UPDATE flagged_for_review
    SET status = 'processing'
    WHERE id = p_flag_id
      AND client_id = p_client_id
      AND status = 'pending'
    RETURNING *;
$$;
