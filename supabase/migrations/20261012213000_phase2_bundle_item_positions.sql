-- Preserve the user-visible item position inside every supplier WhatsApp bundle.
alter table public.supplier_message_bundle_items
  add column if not exists item_position smallint;
alter table public.supplier_message_bundle_items
  drop constraint if exists supplier_message_bundle_items_position_check;
alter table public.supplier_message_bundle_items
  add constraint supplier_message_bundle_items_position_check
    check (item_position is null or (item_position between 1 and 8));
create index if not exists idx_supplier_bundle_positions
  on public.supplier_message_bundle_items(bundle_id,item_position);
create or replace function public.claim_initial_supplier_bundle(
 p_client_id uuid, p_supplier_id uuid, p_limit integer default 8
) returns jsonb language plpgsql security definer set search_path=''
as $$
declare v_ids uuid[]; v_bundle uuid; v_items jsonb;
begin
 if p_limit is null or p_limit<1 or p_limit>8 then
   raise exception 'Batch size must be 1 through 8';
 end if;
 if not exists (select 1 from public.suppliers where id=p_supplier_id and client_id=p_client_id and is_active=true) then
   raise exception 'Supplier not owned by client or inactive';
 end if;
 if exists (select 1 from public.clients where id=p_client_id and automation_paused=true) then
   return null;
 end if;
 select array_agg(id) into v_ids from (
   select bi.id from public.supplier_message_bundle_items bi
   join public.rfqs r on r.id=bi.rfq_id
   join public.rfq_suppliers rs on rs.id=bi.rfq_supplier_id
   where bi.client_id=p_client_id and bi.supplier_id=p_supplier_id
     and bi.kind='initial_rfq' and bi.status='pending' and bi.bundle_id is null
     and bi.available_at<=now() and r.status='active'
     and (r.due_by is null or r.due_by>now())
     and rs.status='sent' and rs.rfq_id=bi.rfq_id and rs.supplier_id=p_supplier_id
   order by bi.available_at,bi.created_at,bi.id
   limit p_limit for update of bi skip locked
 ) claimable;
 if v_ids is null or cardinality(v_ids)=0 then return null; end if;
 insert into public.supplier_message_bundles(client_id,supplier_id,kind,status)
 values(p_client_id,p_supplier_id,'initial_rfq','processing') returning id into v_bundle;
 with ranked as (
   select id,row_number() over(order by available_at,created_at,id) as position
   from public.supplier_message_bundle_items where id=any(v_ids)
 )
 update public.supplier_message_bundle_items bi
   set bundle_id=v_bundle,status='processing',item_position=ranked.position,updated_at=now()
 from ranked where bi.id=ranked.id;
 select jsonb_agg(jsonb_build_object(
    'id',bi.id,'rfq_id',bi.rfq_id,'rfq_supplier_id',bi.rfq_supplier_id,
    'item_position',bi.item_position,
    'product_name',r.product_name,'specs',r.specs,'quantity',r.quantity,
    'deadline_hours',r.deadline_hours,'required_delivery_days',r.required_delivery_days
 ) order by bi.item_position) into v_items
 from public.supplier_message_bundle_items bi
 join public.rfqs r on r.id=bi.rfq_id where bi.id=any(v_ids);
 return jsonb_build_object('bundle_id',v_bundle,'items',v_items);
end $$;
revoke all on function public.claim_initial_supplier_bundle(uuid,uuid,integer)
 from public,anon,authenticated;
grant execute on function public.claim_initial_supplier_bundle(uuid,uuid,integer)
 to service_role;
