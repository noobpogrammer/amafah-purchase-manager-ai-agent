-- Permit repeated supplier revisions while preserving the originally accepted quote.
create or replace function public.mark_supplier_quote_revision(
  p_client_id uuid, p_supplier_id uuid, p_rfq_id uuid, p_new_quote_id uuid
) returns jsonb language plpgsql security definer set search_path=''
as $$
declare v_link public.rfq_suppliers%rowtype;
begin
  select rs.* into v_link from public.rfq_suppliers rs
  join public.rfqs r on r.id=rs.rfq_id
  join public.suppliers s on s.id=rs.supplier_id
  where r.client_id=p_client_id and s.client_id=p_client_id
    and rs.rfq_id=p_rfq_id and rs.supplier_id=p_supplier_id
  for update of rs;
  if not found then raise exception 'Invalid tenant supplier RFQ link'; end if;
  if not exists (select 1 from public.quotes q
       where q.id=p_new_quote_id and q.rfq_id=p_rfq_id and q.supplier_id=p_supplier_id
         and q.is_available=true and q.price is not null) then
    raise exception 'Revision quote not found';
  end if;
  if v_link.commercial_state='revision_pending' then
    if v_link.pending_revision_quote_id=p_new_quote_id then
      return jsonb_build_object('status','revision_pending',
        'revision_quote_id',p_new_quote_id,'unchanged',true);
    end if;
    -- Previous revision was never accepted; it remains available as history.
    update public.rfq_suppliers set pending_revision_quote_id=p_new_quote_id
      where id=v_link.id;
    return jsonb_build_object('status','revision_pending','revision_quote_id',p_new_quote_id);
  end if;
  if v_link.current_accepted_quote_id is null then
    raise exception 'No accepted deal to revise';
  end if;
  if v_link.current_accepted_quote_id=p_new_quote_id then
    raise exception 'Current accepted quote cannot revise itself';
  end if;
  update public.quotes set is_current_accepted=false,
      superseded_at=now(),superseded_by_quote_id=p_new_quote_id
    where id=v_link.current_accepted_quote_id;
  update public.rfq_suppliers set current_accepted_quote_id=null,
      commercial_state='revision_pending',pending_revision_quote_id=p_new_quote_id
    where id=v_link.id;
  return jsonb_build_object('status','revision_pending',
    'historical_accepted_quote_id',v_link.current_accepted_quote_id,
    'revision_quote_id',p_new_quote_id);
end $$;
revoke all on function public.mark_supplier_quote_revision(uuid,uuid,uuid,uuid)
  from public, anon, authenticated;
grant execute on function public.mark_supplier_quote_revision(uuid,uuid,uuid,uuid)
  to service_role;
