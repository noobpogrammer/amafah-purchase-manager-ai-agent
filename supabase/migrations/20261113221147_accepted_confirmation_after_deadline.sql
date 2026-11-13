-- Accepted confirmation is the remaining delivery step, not a new negotiation after deadline.
create or replace function public.claim_commercial_message_approval(p_client_id uuid,p_supplier_id uuid,p_body text)
returns table(approval_id uuid,rfq_id uuid) language plpgsql security definer set search_path='' as $$
declare remaining text:=trim(p_body); a record; ids uuid[]:='{}'; candidate text; ambiguous boolean;
begin
 if remaining='' then return; end if;
 for a in select c.* from public.commercial_action_intents c join public.rfqs r on r.id=c.rfq_id
 join public.suppliers s on s.id=c.supplier_id
 where c.client_id=p_client_id and c.supplier_id=p_supplier_id and s.client_id=p_client_id and s.is_active is not false
 and c.outcome='APPROVE' and c.consumed_at is null and c.approved_message<>'' and c.created_at>now()-interval '10 minutes'
 and ((c.action_type='system_notice' and c.guard_snapshot->>'issued_by'='backend')
 or (r.status='active' and (r.due_by>=now() or (c.action_type in ('accept','confirm','award','accept_quote') and c.acceptance_consumed_at is not null and exists(select 1 from public.quotes aq where aq.id=c.quote_id and aq.is_current_accepted=true)) or exists(select 1 from public.negotiation_sessions ns where ns.rfq_id=r.id and ns.supplier_id=p_supplier_id and ns.status in ('active','awaiting_human_review','awaiting_authorization')))))
 and (c.action_type not in ('accept','confirm','award','accept_quote') or c.acceptance_consumed_at is not null)
 and (c.action_type not in ('counter','counteroffer','negotiate','accept','confirm','award','accept_quote') or
      (c.proposal->>'price')::numeric<=coalesce((select (pc.constraints->>'max')::numeric from public.rfq_negotiation_constraints pc where pc.rfq_id=r.id and pc.dimension='price' and pc.status='authorized' limit 1),r.acceptable_price_max,case when r.acceptable_price_min is null and r.last_quote>0 then r.last_quote+5 end))
 and (c.action_type not in ('counter','counteroffer','negotiate','accept','confirm','award','accept_quote') or exists(
     select 1 from public.quotes q where q.id=(c.guard_snapshot->'current_offer'->>'id')::uuid
     and q.rfq_id=c.rfq_id and q.supplier_id=c.supplier_id and q.is_available=true
     and q.price=(c.guard_snapshot->'current_offer'->>'price')::numeric
     and not exists(select 1 from public.quotes n where n.rfq_id=q.rfq_id and n.supplier_id=q.supplier_id
       and (n.created_at,n.id)>(q.created_at,q.id)
       and (coalesce(lower(trim(n.variant_label)),'')='' or coalesce(lower(trim(n.variant_label)),'')=coalesce(lower(trim(c.guard_snapshot->'current_offer'->>'variant_label')),'')))))
 and (c.action_type not in ('counter','counteroffer','negotiate','accept','confirm','award','accept_quote') or (
  (c.proposal->>'quantity')::integer>0 and (c.proposal->>'delivery_days')::integer>=0
  and (r.quantity is null or (c.proposal->>'quantity')::integer=r.quantity or
    ((coalesce((to_jsonb(r)->>'quantity_flexible')::boolean,false) or exists(select 1 from public.rfq_negotiation_constraints qc where qc.rfq_id=r.id and qc.dimension='quantity' and qc.status='authorized'))
     and (c.proposal->>'quantity')::integer between
       coalesce((select (qc.constraints->>'min')::integer from public.rfq_negotiation_constraints qc where qc.rfq_id=r.id and qc.dimension='quantity' and qc.status='authorized' limit 1),1)
       and coalesce((select (qc.constraints->>'max')::integer from public.rfq_negotiation_constraints qc where qc.rfq_id=r.id and qc.dimension='quantity' and qc.status='authorized' limit 1),(to_jsonb(r)->>'quantity_max')::integer,2147483647)))
  and (r.required_delivery_days is null or (c.proposal->>'delivery_days')::integer<=r.required_delivery_days or
    ((coalesce((to_jsonb(r)->>'delivery_flexible')::boolean,false) or exists(select 1 from public.rfq_negotiation_constraints dc where dc.rfq_id=r.id and dc.dimension='delivery' and dc.status='authorized'))
     and (c.proposal->>'delivery_days')::integer<=coalesce((select (dc.constraints->>'max_days')::integer from public.rfq_negotiation_constraints dc where dc.rfq_id=r.id and dc.dimension='delivery' and dc.status='authorized' limit 1),(to_jsonb(r)->>'max_delivery_days')::integer,2147483647)))
  and not exists(select 1 from jsonb_each(c.guard_snapshot->'current_offer'->'term_sources') src
    left join public.quotes sq on sq.id=(src.value->>'quote_id')::uuid and sq.rfq_id=r.id and sq.supplier_id=c.supplier_id
    where sq.id is null or (to_jsonb(sq)->src.key) is distinct from (src.value->'value'))
 ))
 order by length(c.approved_message) desc,c.created_at desc for update of c skip locked
 loop
   candidate=a.approved_message;
   if position(candidate in remaining)=0 then continue; end if;
   select exists(select 1 from public.commercial_action_intents c where c.client_id=p_client_id and c.supplier_id=p_supplier_id
      and c.rfq_id<>a.rfq_id and c.outcome='APPROVE' and c.consumed_at is null and c.approved_message=candidate
      and c.created_at>now()-interval '10 minutes') into ambiguous;
   if ambiguous then return; end if;
   ids=array_append(ids,a.id);
   remaining=replace(remaining,candidate,'');
 end loop;
 if cardinality(ids)=0 or regexp_replace(remaining,'\s','','g')<>'' then return; end if;
 return query update public.commercial_action_intents c set consumed_at=now() where c.id=any(ids) returning c.id,c.rfq_id;
end $$;
revoke all on function public.claim_commercial_message_approval(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.claim_commercial_message_approval(uuid,uuid,text) to service_role;
