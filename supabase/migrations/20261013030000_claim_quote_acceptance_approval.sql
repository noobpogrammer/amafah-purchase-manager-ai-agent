CREATE OR REPLACE FUNCTION public.claim_quote_acceptance_approval(
 p_approval_id uuid,p_client_id uuid,p_supplier_id uuid,p_rfq_id uuid,p_quote_id uuid
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE claimed uuid;
BEGIN
 UPDATE public.commercial_action_intents a SET consumed_at=now()
 WHERE a.id=p_approval_id AND a.client_id=p_client_id
 AND a.supplier_id=p_supplier_id AND a.rfq_id=p_rfq_id
 AND a.quote_id=p_quote_id AND a.outcome='APPROVE'
 AND a.action_type IN ('accept','confirm','award','accept_quote')
 AND a.consumed_at IS NULL AND a.created_at>now()-interval '10 minutes'
 AND EXISTS (SELECT 1 FROM public.quotes q WHERE q.id=p_quote_id AND q.rfq_id=p_rfq_id AND q.supplier_id=p_supplier_id
   AND q.is_available=true AND q.price=(a.proposal->>'price')::numeric
   AND q.quantity=(a.proposal->>'quantity')::integer)
 AND EXISTS (SELECT 1 FROM public.rfqs r WHERE r.id=p_rfq_id AND r.client_id=p_client_id
   AND r.status='active' AND (r.acceptable_price_max IS NULL OR
   (a.proposal->>'price')::numeric<=r.acceptable_price_max))
 RETURNING a.id INTO claimed;
 RETURN claimed IS NOT NULL;
END; $$;
REVOKE ALL ON FUNCTION public.claim_quote_acceptance_approval(uuid,uuid,uuid,uuid,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_quote_acceptance_approval(uuid,uuid,uuid,uuid,uuid) TO service_role;