-- Serialize buyer-review creation and deadline claims on the RFQ row.
do $migration$
declare definition text; updated text;
begin
 select pg_get_functiondef('public.queue_buyer_final_offer(uuid,uuid,uuid,uuid,jsonb)'::regprocedure) into definition;
 updated:=replace(definition,'begin','begin
 perform 1 from public.rfqs where id=p_rfq_id and client_id=p_client_id for update;');
 if updated=definition then raise exception 'Review lock insertion failed';end if;
 execute updated;
 select pg_get_functiondef('public.claim_rfq_for_finalization(uuid)'::regprocedure) into definition;
 updated:=replace(definition,'BEGIN','BEGIN
    PERFORM 1 FROM public.rfqs WHERE id=p_rfq_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM public.negotiation_sessions WHERE rfq_id=p_rfq_id AND status IN (''active'',''awaiting_human_review'',''awaiting_authorization'')) THEN RETURN FALSE;END IF;');
 if updated=definition then raise exception 'Finalization lock insertion failed';end if;
 execute updated;
end $migration$;
