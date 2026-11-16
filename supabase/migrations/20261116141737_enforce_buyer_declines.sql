-- A buyer decline blocks further commercial actions on this exact quote.
do $migration$
declare definition text; updated text;
begin
 select pg_get_functiondef('public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text)'::regprocedure) into definition;
 updated:=replace(definition,' select * into q from public.quotes', $new$ if exists(select 1 from public.buyer_final_offer_reviews b where b.client_id=p_client_id and b.supplier_id=p_supplier_id and b.rfq_id=p_rfq_id and b.quote_id=p_quote_id and b.status in ('authorized','completed') and b.decision='decline') then return null;end if;
 select * into q from public.quotes$new$);
 if definition=updated then raise exception 'Acceptance decline hook missing';end if;
 execute updated;
 select pg_get_functiondef('public.claim_commercial_message_approval(uuid,uuid,text)'::regprocedure) into definition;
 updated:=replace(definition,' order by length(c.approved_message)', $new$ and (c.action_type not in ('counter','counteroffer','negotiate','accept','confirm','award','accept_quote') or not exists(select 1 from public.buyer_final_offer_reviews b where b.client_id=c.client_id and b.supplier_id=c.supplier_id and b.rfq_id=c.rfq_id and b.quote_id=c.quote_id and b.status in ('authorized','completed') and b.decision='decline'))
 order by length(c.approved_message)$new$);
 if definition=updated then raise exception 'Send decline hook missing';end if;
 execute updated;
end $migration$;
