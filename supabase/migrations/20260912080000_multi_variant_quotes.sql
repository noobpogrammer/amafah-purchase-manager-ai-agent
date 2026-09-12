-- ============================================================
-- Phase 10: Multi-Variant Quote Support
-- Adds variant_label, quote_group_id, is_available, source_message_id to quotes
-- Adds atomic record_quote_variants RPC
-- ============================================================

-- 1. Add new columns to quotes table
ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS variant_label TEXT NULL,
    ADD COLUMN IF NOT EXISTS quote_group_id UUID NULL,
    ADD COLUMN IF NOT EXISTS is_available BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS source_message_id UUID NULL REFERENCES message_log(id) ON DELETE SET NULL;

-- 2. Create index for effective variant queries
CREATE INDEX IF NOT EXISTS idx_quotes_effective_variant
ON quotes (
    rfq_id,
    supplier_id,
    variant_label,
    created_at DESC
);

-- 3. Atomic batch quote variant recording function
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
SET search_path = public
AS $$
DECLARE
    v_rfq_status TEXT;
    v_has_available_priced_variant BOOLEAN := FALSE;
    v_item JSONB;
    v_price NUMERIC;
    v_avail BOOLEAN;
BEGIN
    -- Check that RFQ exists and is active
    SELECT status INTO v_rfq_status
    FROM rfqs
    WHERE id = p_rfq_id;

    IF v_rfq_status IS NULL OR v_rfq_status != 'active' THEN
        RETURN;
    END IF;

    -- Iterate and insert each variant
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_variants)
    LOOP
        v_price := CASE 
            WHEN v_item->>'price' IS NOT NULL AND v_item->>'price' != '' 
            THEN (v_item->>'price')::NUMERIC 
            ELSE NULL 
        END;
        
        v_avail := COALESCE((v_item->>'is_available')::BOOLEAN, TRUE);

        IF v_avail = TRUE AND v_price IS NOT NULL AND v_price > 0 THEN
            v_has_available_priced_variant := TRUE;
        END IF;

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
            NULLIF(TRIM(v_item->>'variant_label'), ''),
            v_price,
            v_item->>'delivery_time',
            v_item->>'quality_notes',
            p_raw_message,
            COALESCE(p_confidence, 'high'),
            v_avail,
            p_source_message_id
        )
        RETURNING *;
    END LOOP;

    -- If at least one valid priced available variant was recorded, mark supplier as responded
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
