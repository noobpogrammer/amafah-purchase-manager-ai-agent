-- Atomic, tenant-scoped recording of a multi-RFQ supplier price list.
-- One inbound message either records all valid lines or no lines.
create or replace function public.record_multi_rfqs_atomic(
  p_client_id uuid,
  p_supplier_id uuid,
  p_source_message_id uuid,
  p_raw_message text,
  p_items jsonb
) returns jsonb
language plpgsql security definer set search_path=''
as $$
declare
  v_item jsonb;
  v_rfq_id uuid;
  v_price numeric; v_available boolean;
  v_conf numeric;
  v_variant text;
  v_qty integer;
  v_quote_id uuid;
  v_quote_group_id uuid := gen_random_uuid();
  v_inserted integer := 0;
  v_existing integer := 0;
  v_output jsonb := '[]'::jsonb;
begin
  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) < 2 then
    raise exception 'Two or more itemized quotes required';
  end if;
  if (select count(distinct item->>'candidate_rfq_id')
        from jsonb_array_elements(p_items) item) < 2 then
    raise exception 'At least two distinct RFQs must be identified';
  end if;
  if not exists (
    select 1 from public.message_log
    where id=p_source_message_id and client_id=p_client_id
      and supplier_id=p_supplier_id and direction='inbound'
  ) then
    raise exception 'Source message is not a tenant-owned inbound message';
  end if;

  -- Serialize all retries for one supplier inbound message.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(p_source_message_id::text));

  -- Validate the ENTIRE payload before inserting any quotes.
  for v_item in select value from jsonb_array_elements(p_items) loop
    if nullif(v_item->>'candidate_rfq_id','') is null or (coalesce((v_item->>'is_available')::boolean,true) and nullif(v_item->>'price','') is null)
       or nullif(v_item->>'confidence','') is null then
      raise exception 'Incomplete quote mapping';
    end if;
    v_rfq_id := (v_item->>'candidate_rfq_id')::uuid;
    v_available := coalesce((v_item->>'is_available')::boolean,true);
    v_price := case when v_available then (v_item->>'price')::numeric else null end;
    v_conf := (v_item->>'confidence')::numeric;
    if (v_available and (v_price is null or v_price <= 0 or v_price::text in ('NaN','Infinity','-Infinity'))) or v_conf is null or v_conf < 0.70 or v_conf > 1 then
      raise exception 'Invalid price or quote confidence';
    end if;
    if nullif(v_item->>'quantity','') is not null and (v_item->>'quantity')::integer <= 0 then
      raise exception 'Invalid quantity';
    end if;
    if not exists (
      select 1 from public.rfqs r
      join public.rfq_suppliers rs on rs.rfq_id=r.id
      join public.suppliers s on s.id=rs.supplier_id
      where r.id=v_rfq_id and r.client_id=p_client_id
        and s.id=p_supplier_id and s.client_id=p_client_id and s.is_active=true and s.deleted_at is null
        and rs.status in ('sent','responded','clarifying','revision_pending')
        and r.status='active'
        and (
          r.due_by is null or r.due_by > now()
          or exists (
            select 1 from public.negotiation_sessions n
            where n.client_id=p_client_id and n.supplier_id=p_supplier_id
              and n.rfq_id=r.id and n.status in ('active','awaiting_authorization','awaiting_human_review')
          )
        )
    ) then
      raise exception 'RFQ is not authorized or open for this supplier';
    end if;
  end loop;

  for v_item in select value from jsonb_array_elements(p_items) loop
    v_rfq_id := (v_item->>'candidate_rfq_id')::uuid;
    v_available := coalesce((v_item->>'is_available')::boolean,true);
    v_price := case when v_available then (v_item->>'price')::numeric else null end;
    v_variant := nullif(v_item->>'variant_label','');
    v_qty := nullif(v_item->>'quantity','')::integer;
    select id into v_quote_id from public.quotes
    where rfq_id=v_rfq_id and supplier_id=p_supplier_id
      and source_message_id=p_source_message_id
      and price is not distinct from v_price and is_available=v_available and variant_label is not distinct from v_variant
      and quantity is not distinct from v_qty
    order by created_at limit 1;
    if not found and exists(select 1 from public.quotes where rfq_id=v_rfq_id and supplier_id=p_supplier_id and source_message_id=p_source_message_id and variant_label is not distinct from v_variant) then
      raise exception 'Source already recorded with different facts; preserve history';
    end if;
    if found then
      v_existing := v_existing + 1;
    else
      insert into public.quotes(
        rfq_id,supplier_id,source_message_id,quote_group_id,raw_message,
        confidence,price,variant_label,quantity,delivery_time,quality_notes,is_available
      ) values (
        v_rfq_id,p_supplier_id,p_source_message_id,v_quote_group_id,p_raw_message,
        'high',v_price,v_variant,v_qty,nullif(v_item->>'delivery_time',''),
        nullif(v_item->>'quality_notes',''),v_available
      ) returning id into v_quote_id;
      v_inserted := v_inserted + 1;
    end if;
    update public.rfq_suppliers set status='responded'
      where supplier_id=p_supplier_id and rfq_id=v_rfq_id
        and status in ('sent','clarifying');
    v_output := v_output || jsonb_build_array(jsonb_build_object(
      'rfq_id', v_rfq_id, 'quote_id', v_quote_id, 'price', v_price, 'is_available', v_available
    ));
  end loop;
  update public.pending_commercial_drafts set status='resolved', resolved_rfq_id=null, extracted_facts=extracted_facts||jsonb_build_object('recorded_items',v_output) where client_id=p_client_id and supplier_id=p_supplier_id and source_message_id=p_source_message_id::text and status='awaiting_rfq_resolution';
  return jsonb_build_object('status','recorded','inserted',v_inserted,
                            'duplicates',v_existing,'quotes',v_output);
end $$;
revoke all on function public.record_multi_rfqs_atomic(uuid,uuid,uuid,text,jsonb)
  from public, anon, authenticated;
grant execute on function public.record_multi_rfqs_atomic(uuid,uuid,uuid,text,jsonb)
  to service_role;

-- Buyer decisions also cover quantity shortfalls, without changing the RFQ globally.
alter table public.buyer_final_offer_reviews add column partial_quantity_approved boolean not null default false;
alter table public.buyer_final_offer_reviews add column review_kind text not null default 'final_price' check(review_kind in ('final_price','quantity'));
alter table public.buyer_final_offer_reviews drop constraint buyer_final_offer_reviews_decision_check;
alter table public.buyer_final_offer_reviews add constraint buyer_final_offer_reviews_decision_check check(decision in ('accept','negotiate','partial','decline'));

create or replace function public.buyer_partial_quantity_authorized(p_client_id uuid,p_supplier_id uuid,p_rfq_id uuid,p_quote_id uuid,p_quantity integer)
returns boolean language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.buyer_final_offer_reviews b join public.quotes q on q.id=b.quote_id
 where b.client_id=p_client_id and b.supplier_id=p_supplier_id and b.rfq_id=p_rfq_id and b.quote_id=p_quote_id
 and b.status in ('pending','authorized','completed') and b.partial_quantity_approved=true
 and b.offer_snapshot->>'quantity'=p_quantity::text and q.quantity=p_quantity and q.is_available=true
 and q.supplier_id=p_supplier_id and q.rfq_id=p_rfq_id
 and (b.offer_snapshot->>'price')::numeric=q.price
 and not exists(select 1 from public.quotes n where n.rfq_id=q.rfq_id and n.supplier_id=q.supplier_id
 and (n.created_at,n.id)>(q.created_at,q.id) and (coalesce(n.variant_label,'')='' or coalesce(n.variant_label,'')=coalesce(q.variant_label,''))))
$$;
revoke all on function public.buyer_partial_quantity_authorized(uuid,uuid,uuid,uuid,integer) from public,anon,authenticated;
grant execute on function public.buyer_partial_quantity_authorized(uuid,uuid,uuid,uuid,integer) to service_role;

do $migration$
declare definition text; updated text;
begin
 select pg_get_functiondef('public.decide_buyer_final_offer(uuid,uuid,uuid,text,numeric)'::regprocedure) into definition;
 updated:=replace(definition,$old$p_decision not in ('accept','negotiate')$old$,$new$p_decision not in ('accept','negotiate','partial','decline')$new$);
 updated:=replace(updated,$old$if p_decision not in$old$,$new$if p_decision in ('partial','decline') and p_counter is not null then raise exception 'Decision cannot include counter';end if;
 if p_decision='partial' and review.review_kind<>'quantity' then raise exception 'Quantity review required';end if;
 if p_decision not in$new$);
 if definition=updated then raise exception 'Buyer decision hook missing';end if;
 updated:=replace(updated,$old$set status='authorized',decision=p_decision$old$,$new$set partial_quantity_approved=(case when p_decision='partial' then true when p_decision='decline' then false else partial_quantity_approved end),status='authorized',decision=p_decision$new$);
 execute updated;
 -- Quantity permission stays scoped to this quote; price and delivery checks remain unchanged.
 select pg_get_functiondef('public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text)'::regprocedure) into definition;
 updated:=replace(definition,'if r.quantity is not null and qty<>r.quantity then',
 'if r.quantity is not null and qty<>r.quantity and not public.buyer_partial_quantity_authorized(p_client_id,p_supplier_id,p_rfq_id,p_quote_id,qty) then');
 if definition=updated then raise exception 'Acceptance quantity hook missing';end if;
 execute updated;
 select pg_get_functiondef('public.claim_commercial_message_approval(uuid,uuid,text)'::regprocedure) into definition;
 updated:=replace(definition,$old$(c.proposal->>'quantity')::integer=r.quantity or$old$,$new$(c.proposal->>'quantity')::integer=r.quantity or public.buyer_partial_quantity_authorized(c.client_id,c.supplier_id,c.rfq_id,c.quote_id,(c.proposal->>'quantity')::integer) or$new$);
 if definition=updated then raise exception 'Send quantity hook missing';end if;
 execute updated;
end $migration$;
