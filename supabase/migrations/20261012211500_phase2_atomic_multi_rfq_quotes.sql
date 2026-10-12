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
  v_price numeric;
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
    if nullif(v_item->>'candidate_rfq_id','') is null or nullif(v_item->>'price','') is null
       or nullif(v_item->>'confidence','') is null then
      raise exception 'Incomplete quote mapping';
    end if;
    v_rfq_id := (v_item->>'candidate_rfq_id')::uuid;
    v_price := (v_item->>'price')::numeric;
    v_conf := (v_item->>'confidence')::numeric;
    if v_price <= 0 or v_conf < 0.70 or v_conf > 1 then
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
        and s.id=p_supplier_id and s.client_id=p_client_id
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
    v_price := (v_item->>'price')::numeric;
    v_variant := nullif(v_item->>'variant_label','');
    v_qty := nullif(v_item->>'quantity','')::integer;
    select id into v_quote_id from public.quotes
    where rfq_id=v_rfq_id and supplier_id=p_supplier_id
      and source_message_id=p_source_message_id
      and price=v_price and variant_label is not distinct from v_variant
      and quantity is not distinct from v_qty
    order by created_at limit 1;
    if found then
      v_existing := v_existing + 1;
    else
      insert into public.quotes(
        rfq_id,supplier_id,source_message_id,quote_group_id,raw_message,
        confidence,price,variant_label,quantity,delivery_time,quality_notes,is_available
      ) values (
        v_rfq_id,p_supplier_id,p_source_message_id,v_quote_group_id,p_raw_message,
        'high',v_price,v_variant,v_qty,nullif(v_item->>'delivery_time',''),
        nullif(v_item->>'quality_notes',''),true
      ) returning id into v_quote_id;
      v_inserted := v_inserted + 1;
    end if;
    update public.rfq_suppliers set status='responded'
      where supplier_id=p_supplier_id and rfq_id=v_rfq_id
        and status in ('sent','clarifying');
    v_output := v_output || jsonb_build_array(jsonb_build_object(
      'rfq_id', v_rfq_id, 'quote_id', v_quote_id, 'price', v_price
    ));
  end loop;
  return jsonb_build_object('status','recorded','inserted',v_inserted,
                            'duplicates',v_existing,'quotes',v_output);
end $$;
revoke all on function public.record_multi_rfqs_atomic(uuid,uuid,uuid,text,jsonb)
  from public, anon, authenticated;
grant execute on function public.record_multi_rfqs_atomic(uuid,uuid,uuid,text,jsonb)
  to service_role;
