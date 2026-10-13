-- Durable, single-use authorization for commercial WhatsApp sends.
-- Approvals are held in the audited commercial_action_intents table.
ALTER TABLE public.commercial_action_intents
  ADD COLUMN IF NOT EXISTS approved_message text,
  ADD COLUMN IF NOT EXISTS consumed_at timestamptz,
  ADD COLUMN IF NOT EXISTS consumed_message_id text;

CREATE INDEX IF NOT EXISTS commercial_approvals_claim_idx
 ON public.commercial_action_intents (client_id, supplier_id, created_at DESC)
 WHERE outcome = 'APPROVE' AND consumed_at IS NULL;

CREATE OR REPLACE FUNCTION public.claim_commercial_message_approval(
  p_client_id uuid,
  p_supplier_id uuid,
  p_body text
) RETURNS TABLE(approval_id uuid, rfq_id uuid)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = public
AS $$
BEGIN
  RETURN QUERY
  WITH candidate AS (
    SELECT c.id
    FROM public.commercial_action_intents AS c
    JOIN public.rfqs AS r ON r.id = c.rfq_id AND r.client_id = c.client_id
    JOIN public.rfq_suppliers AS rs ON rs.rfq_id = c.rfq_id AND rs.supplier_id = c.supplier_id
    WHERE c.client_id = p_client_id
      AND c.supplier_id = p_supplier_id
      AND c.outcome = 'APPROVE'
      AND c.approved_message = p_body
      AND c.consumed_at IS NULL
      AND c.created_at > now() - interval '15 minutes'
      AND r.status = 'active'
      AND (r.due_by >= now() OR EXISTS (
        SELECT 1 FROM public.negotiation_sessions ns
        WHERE ns.rfq_id = c.rfq_id AND ns.supplier_id = c.supplier_id
          AND ns.client_id = c.client_id AND ns.status = 'active'
      ))
      AND NOT EXISTS (
        SELECT 1 FROM public.commercial_action_intents other
        JOIN public.rfqs other_rfq ON other_rfq.id = other.rfq_id
        WHERE other.client_id = c.client_id
          AND other.supplier_id = c.supplier_id
          AND other.outcome = 'APPROVE'
          AND other.approved_message = c.approved_message
          AND other.consumed_at IS NULL
          AND other.created_at > now() - interval '15 minutes'
          AND other.rfq_id <> c.rfq_id
          AND other_rfq.status = 'active'
      )
    ORDER BY c.created_at DESC
    LIMIT 1
    FOR UPDATE OF c SKIP LOCKED
  )
  UPDATE public.commercial_action_intents c
  SET consumed_at = now()
  FROM candidate
  WHERE c.id = candidate.id
  RETURNING c.id, c.rfq_id;
END;
$$;
REVOKE ALL ON FUNCTION public.claim_commercial_message_approval(uuid,uuid,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_commercial_message_approval(uuid,uuid,text) TO service_role;
