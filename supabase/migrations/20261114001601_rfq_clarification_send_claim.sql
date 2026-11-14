-- An RFQ clarification is a single-use permission to ask, never permission to buy.
create or replace function public.claim_rfq_clarification(p_client_id uuid,p_supplier_id uuid,p_body text)
returns uuid language plpgsql security definer set search_path='' as $$
declare selected_id uuid;
begin
 if p_body <> 'Could you please confirm which product your message refers to?' then return null; end if;
 select c.id into selected_id from public.commercial_action_intents c
 join public.suppliers s on s.id=c.supplier_id and s.client_id=c.client_id
 where c.client_id=p_client_id and c.supplier_id=p_supplier_id and s.is_active is not false
 and c.action_type='clarify_rfq' and c.outcome='APPROVE' and c.rfq_id is null and c.quote_id is null
 and c.proposal='{}'::jsonb and c.approved_message=p_body and c.consumed_at is null
 and c.created_at>now()-interval '10 minutes'
 and exists(select 1 from public.rfq_suppliers rs join public.rfqs r on r.id=rs.rfq_id
 where rs.supplier_id=p_supplier_id and r.client_id=p_client_id and r.status='active'
 and c.guard_snapshot->'candidate_rfq_ids' ? r.id::text)
 order by c.created_at desc limit 1 for update of c skip locked;
 if selected_id is null then return null; end if;
 update public.commercial_action_intents set consumed_at=now() where id=selected_id;
 return selected_id;
end $$;
revoke all on function public.claim_rfq_clarification(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.claim_rfq_clarification(uuid,uuid,text) to service_role;
