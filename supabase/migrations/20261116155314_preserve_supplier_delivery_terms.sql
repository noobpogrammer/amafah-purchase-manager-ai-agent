-- Preserve the difference between a delivery duration, deadline and lower bound.
create or replace function public.parse_supplier_delivery_term(p_text text)
returns jsonb language plpgsql immutable set search_path='' as $$
declare raw text:=trim(coalesce(p_text,'')); normalized text; parts text[]; relation text; days integer; unit text;
begin
 normalized:=lower(regexp_replace(raw,'\s+',' ','g'));
 parts:=regexp_match(normalized,'^(delivery:?[[:space:]]+)?((within|after|in)[[:space:]]*)?([0-9]+)[[:space:]]*((working|business)[[:space:]]+)?days?([[:space:]]+delivery)?$');
 if parts is not null then
  relation:=case parts[3] when 'after' then 'after' when 'within' then 'within' else 'exact' end;
  days:=parts[4]::integer; unit:=case when parts[6] is not null then 'business_days' else 'calendar_days' end;
 elsif normalized in ('next day','next day delivery','next-day delivery','tomorrow') then
  relation:='exact';days:=1;unit:='calendar_days';
 elsif normalized in ('same day','same-day delivery','today','immediate') then
  relation:='exact';days:=0;unit:='calendar_days';
 else return null;end if;
 return jsonb_build_object('relation',relation,'days',days,'unit',unit,'text',raw);
 exception when numeric_value_out_of_range then return null;
end $$;
revoke all on function public.parse_supplier_delivery_term(text) from public,anon,authenticated;
grant execute on function public.parse_supplier_delivery_term(text) to service_role;

create or replace function public.valid_offer_delivery_acceptance(p_client_id uuid,p_supplier_id uuid,p_rfq_id uuid,p_quote_id uuid,p_offer jsonb,p_proposal jsonb)
returns boolean language plpgsql stable security definer set search_path='' as $$
declare term jsonb; r public.rfqs%rowtype; c public.rfq_negotiation_constraints%rowtype; cap integer;
begin
 select * into r from public.rfqs where id=p_rfq_id and client_id=p_client_id;
 if not found then return false;end if;
 term:=public.parse_supplier_delivery_term(p_offer#>>'{term_sources,delivery_time,value}');
 if term is null or (term->>'days')::integer is distinct from (p_offer->>'resolved_delivery_days')::integer
 or (term->>'days')::integer is distinct from (p_proposal->>'delivery_days')::integer then return false;end if;
 if p_offer ? 'delivery_term' and p_offer->'delivery_term' is distinct from term then return false;end if;
 if p_proposal ? 'delivery_term' and p_proposal->'delivery_term' is distinct from term then return false;end if;
 cap:=r.required_delivery_days;
 select * into c from public.rfq_negotiation_constraints where client_id=p_client_id and rfq_id=p_rfq_id and dimension='delivery' and status='authorized' limit 1;
 if found then cap:=(c.constraints->>'max_days')::integer;
 elsif coalesce((to_jsonb(r)->>'delivery_flexible')::boolean,false) then cap:=(to_jsonb(r)->>'max_delivery_days')::integer;end if;
 if term->>'relation'='after' or term->>'unit'='business_days' then
  -- A lower bound or business-day duration does not guarantee a calendar deadline.
  if cap is not null or p_offer->'delivery_term' is distinct from term or p_proposal->'delivery_term' is distinct from term then return false;end if;
  if not exists(select 1 from public.buyer_final_offer_reviews b where b.client_id=p_client_id and b.supplier_id=p_supplier_id and b.rfq_id=p_rfq_id and b.quote_id=p_quote_id
   and b.status in ('authorized','completed') and b.decision='accept' and b.offer_snapshot->'delivery_term'=term
   and b.offer_snapshot->'term_sources'=p_offer->'term_sources'
   and (b.offer_snapshot->>'price')::numeric=(p_offer->>'price')::numeric
   and b.offer_snapshot->>'quantity'=p_offer->>'quantity') then return false;end if;
 elsif cap is not null and (term->>'days')::integer>cap then return false;end if;
 return true;
end $$;
revoke all on function public.valid_offer_delivery_acceptance(uuid,uuid,uuid,uuid,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.valid_offer_delivery_acceptance(uuid,uuid,uuid,uuid,jsonb,jsonb) to service_role;

do $migration$
declare definition text; updated text;
begin
 select pg_get_functiondef('public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text)'::regprocedure) into definition;
 updated:=replace(definition,$old$ qty=(p_offer->>'quantity')::integer;$old$,$new$ if not public.valid_offer_delivery_acceptance(p_client_id,p_supplier_id,p_rfq_id,p_quote_id,p_offer,a.proposal) then return null;end if;
 qty=(p_offer->>'quantity')::integer;$new$);
 if definition=updated then raise exception 'Delivery acceptance hook missing';end if;
 execute updated;
 select pg_get_functiondef('public.claim_commercial_message_approval(uuid,uuid,text)'::regprocedure) into definition;
 updated:=replace(definition,' order by length(c.approved_message)',$new$ and (c.action_type not in ('accept','confirm','award','accept_quote') or public.valid_offer_delivery_acceptance(c.client_id,c.supplier_id,c.rfq_id,c.quote_id,c.guard_snapshot->'current_offer',c.proposal))
 order by length(c.approved_message)$new$);
 if definition=updated then raise exception 'Delivery send hook missing';end if;
 execute updated;
end $migration$;
