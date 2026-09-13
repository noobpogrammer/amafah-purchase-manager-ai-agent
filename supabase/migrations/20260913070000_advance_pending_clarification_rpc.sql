-- 20260913070000_advance_pending_clarification_rpc.sql
-- Database-authoritative atomic transition for advancing pending clarifications

-- 1. Partial unique index guaranteeing at most one active clarification per client+supplier
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_clarifications_unique_active
ON pending_clarifications (client_id, supplier_id)
WHERE status = 'awaiting_reply';

-- 2. Atomic advance RPC
CREATE OR REPLACE FUNCTION advance_pending_clarification(
    p_previous_id UUID,
    p_client_id UUID,
    p_supplier_id UUID,
    p_candidate_rfq_ids UUID[],
    p_raw_message TEXT,
    p_extracted_price NUMERIC,
    p_extracted_delivery TEXT,
    p_extracted_notes TEXT,
    p_round_number INTEGER,
    p_no_progress_count INTEGER,
    p_last_question TEXT
)
RETURNS SETOF pending_clarifications
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_old_row pending_clarifications%ROWTYPE;
    v_new_row pending_clarifications%ROWTYPE;
BEGIN
    -- 1. Lock and verify the existing awaiting_reply row
    SELECT * INTO v_old_row
    FROM pending_clarifications
    WHERE id = p_previous_id
      AND client_id = p_client_id
      AND supplier_id = p_supplier_id
      AND status = 'awaiting_reply'
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- 2. Mark old clarification as abandoned first (frees unique index constraint)
    UPDATE pending_clarifications
    SET status = 'abandoned'
    WHERE id = p_previous_id;

    -- 3. Insert new clarification row
    INSERT INTO pending_clarifications (
        client_id,
        supplier_id,
        pending_rfq_ids,
        raw_message,
        extracted_price,
        extracted_delivery,
        extracted_notes,
        round_number,
        no_progress_count,
        last_question,
        status
    )
    VALUES (
        p_client_id,
        p_supplier_id,
        p_candidate_rfq_ids,
        p_raw_message,
        p_extracted_price,
        p_extracted_delivery,
        p_extracted_notes,
        p_round_number,
        p_no_progress_count,
        p_last_question,
        'awaiting_reply'
    )
    RETURNING * INTO v_new_row;

    -- 4. Update candidate rfq_suppliers status to clarifying
    UPDATE rfq_suppliers
    SET status = 'clarifying'
    WHERE supplier_id = p_supplier_id
      AND rfq_id = ANY(p_candidate_rfq_ids);

    RETURN NEXT v_new_row;
    RETURN;
END;
$$;

REVOKE EXECUTE ON FUNCTION advance_pending_clarification(UUID, UUID, UUID, UUID[], TEXT, NUMERIC, TEXT, TEXT, INTEGER, INTEGER, TEXT) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION advance_pending_clarification(UUID, UUID, UUID, UUID[], TEXT, NUMERIC, TEXT, TEXT, INTEGER, INTEGER, TEXT) FROM anon;
REVOKE EXECUTE ON FUNCTION advance_pending_clarification(UUID, UUID, UUID, UUID[], TEXT, NUMERIC, TEXT, TEXT, INTEGER, INTEGER, TEXT) FROM authenticated;
GRANT EXECUTE ON FUNCTION advance_pending_clarification(UUID, UUID, UUID, UUID[], TEXT, NUMERIC, TEXT, TEXT, INTEGER, INTEGER, TEXT) TO service_role;
