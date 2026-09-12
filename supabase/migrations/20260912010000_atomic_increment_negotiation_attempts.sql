-- ============================================================
-- Atomic increment for negotiation attempts
-- Enforces negotiation_attempts < p_max_attempts atomically
-- ============================================================

CREATE OR REPLACE FUNCTION increment_negotiation_attempts(
    p_rfq_id UUID,
    p_supplier_id UUID,
    p_max_attempts INT DEFAULT 3
)
RETURNS INT
LANGUAGE plpgsql
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
