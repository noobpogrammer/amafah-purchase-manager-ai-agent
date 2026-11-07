-- Match the partial unique index when upserting and preserve explicit supplier quantity.
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
                quantity,
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
                NULLIF(v_item->>'quantity', '')::integer,
                v_price,
                v_item->>'delivery_time',
                v_item->>'quality_notes',
                p_raw_message,
                COALESCE(p_confidence, 'high'),
                v_avail,
                p_source_message_id
            )
            ON CONFLICT (rfq_id, supplier_id, source_message_id, COALESCE(variant_label, ''))
            WHERE source_message_id IS NOT NULL
            DO UPDATE SET
                price = EXCLUDED.price,
                quantity = EXCLUDED.quantity,
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
                quantity,
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
                NULLIF(v_item->>'quantity', '')::integer,
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

