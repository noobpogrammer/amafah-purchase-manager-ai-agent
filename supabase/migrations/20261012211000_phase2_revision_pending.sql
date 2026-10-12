-- Option B: preserve accepted quote history while tracking a withdrawn deal.
alter table public.rfq_suppliers
  add column if not exists commercial_state text not null default 'unaccepted',
  add column if not exists pending_revision_quote_id uuid references public.quotes(id);
alter table public.rfq_suppliers drop constraint if exists rfq_suppliers_commercial_state_check;
alter table public.rfq_suppliers add constraint rfq_suppliers_commercial_state_check
  check (commercial_state in ('unaccepted','accepted','revision_pending'));
create or replace function public.mark_supplier_quote_revision(
  p_client_id uuid, p_supplier_id uuid, p_rfq_id uuid, p_new_quote_id uuid
) returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  v_link public.rfq_suppliers%rowtype;
  v_quote public.quotes%rowtype;
begin
  select rs.* into v_link from public.rfq_suppliers rs
    join public.rfqs r on r.id=rs.rfq_id
    join public.suppliers s on s.id=rs.supplier_id
    where r.client_id=p_client_id and s.client_id=p_client_id
      and rs.rfq_id=p_rfq_id and rs.supplier_id=p_supplier_id for update of rs;
  if not found then raise exception 'Invalid supplier/RFQ/client link'; end if;
  select * into v_quote from public.quotes
    where id=p_new_quote_id and rfq_id=p_rfq_id and supplier_id=p_supplier_id and is_available=true;
  if not found then raise exception 'Revision quote not found for supplier RFQ'; end if;
  if v_link.current_accepted_quote_id is null then
    raise exception 'No current accepted quote to revise';
  end if;
  if v_link.current_accepted_quote_id=p_new_quote_id then
    raise exception 'Accepted quote cannot revise itself';
  end if;
  if v_link.commercial_state='revision_pending' and v_link.pending_revision_quote_id=p_new_quote_id then
    return jsonb_build_object('status','revision_pending','original_quote_id',v_link.current_accepted_quote_id,'revision_quote_id',p_new_quote_id);
  end if;
  update public.quotes set is_current_accepted=false, superseded_at=now(),
    superseded_by_quote_id=p_new_quote_id where id=v_link.current_accepted_quote_id;
  update public.rfq_suppliers set current_accepted_quote_id=null,
    commercial_state='revision_pending', pending_revision_quote_id=p_new_quote_id
    where id=v_link.id;
  return jsonb_build_object('status','revision_pending','original_quote_id',v_link.current_accepted_quote_id,'revision_quote_id',p_new_quote_id);
end $$;
create or replace function public.select_current_supplier_quote(
 p_client_id uuid, p_supplier_id uuid, p_rfq_id uuid, p_quote_id uuid, p_accepted_by text default 'operator'
) returns jsonb language plpgsql security definer set search_path=''
as $$
declare v_link public.rfq_suppliers%rowtype; v_quote public.quotes%rowtype;
begin
 select rs.* into v_link from public.rfq_suppliers rs join public.rfqs r on r.id=rs.rfq_id
 join public.suppliers s on s.id=rs.supplier_id
 where r.client_id=p_client_id and s.client_id=p_client_id and rs.rfq_id=p_rfq_id and rs.supplier_id=p_supplier_id
 for update of rs;
 if not found then raise exception 'Invalid supplier/RFQ/client link'; end if;
 select * into v_quote from public.quotes
 where id=p_quote_id and rfq_id=p_rfq_id and supplier_id=p_supplier_id and is_available=true and price is not null;
 if not found then raise exception 'Quote not found'; end if;
 update public.quotes set is_current_accepted=false, superseded_at=coalesce(superseded_at,now()),
 superseded_by_quote_id=p_quote_id
 where rfq_id=p_rfq_id and supplier_id=p_supplier_id and is_current_accepted=true and id<>p_quote_id;
 update public.quotes set is_accepted=true, is_current_accepted=true,
 accepted_at=coalesce(accepted_at,now()),accepted_by=p_accepted_by,
 superseded_at=null,superseded_by_quote_id=null where id=p_quote_id;
 update public.rfq_suppliers set current_accepted_quote_id=p_quote_id,
 accepted_at=now(),commercial_state='accepted',pending_revision_quote_id=null where id=v_link.id;
 return jsonb_build_object('status','accepted','quote_id',p_quote_id);
end $$;
revoke all on function public.mark_supplier_quote_revision(uuid,uuid,uuid,uuid) from public, anon, authenticated;
revoke all on function public.select_current_supplier_quote(uuid,uuid,uuid,uuid,text) from public, anon, authenticated;
grant execute on function public.mark_supplier_quote_revision(uuid,uuid,uuid,uuid) to service_role;
grant execute on function public.select_current_supplier_quote(uuid,uuid,uuid,uuid,text) to service_role;
