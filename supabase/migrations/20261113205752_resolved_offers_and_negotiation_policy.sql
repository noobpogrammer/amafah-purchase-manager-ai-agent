-- Acceptance and WhatsApp transport are different operations; never consume each other's authorization.
alter table public.commercial_action_intents add column if not exists acceptance_consumed_at timestamptz;

-- Serialize commercial quote revisions with acceptance on their owning RFQ.
create or replace function public.lock_quote_commercial_revision() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 perform id from public.rfqs where id=new.rfq_id for update;
 return new;
end $$;
drop trigger if exists lock_quote_commercial_revision on public.quotes;
create trigger lock_quote_commercial_revision before insert or update of price,quantity,delivery_time,quality_notes,is_available,variant_label
 on public.quotes for each row execute function public.lock_quote_commercial_revision();

create or replace function public.accept_resolved_quote(
 p_approval_id uuid,p_client_id uuid,p_supplier_id uuid,p_rfq_id uuid,p_quote_id uuid,
 p_offer jsonb,p_accepted_by text default 'agent'
) returns jsonb language plpgsql security definer set search_path='' as $$
declare a public.commercial_action_intents%rowtype; r public.rfqs%rowtype;
 q public.quotes%rowtype; link public.rfq_suppliers%rowtype; src record; source_row public.quotes%rowtype;
 cap numeric; qty integer; days integer; c public.rfq_negotiation_constraints%rowtype; selected jsonb;
begin
 select * into r from public.rfqs where id=p_rfq_id and client_id=p_client_id and status='active' for update;
 if not found then return null; end if;
 select * into link from public.rfq_suppliers where rfq_id=p_rfq_id and supplier_id=p_supplier_id for update;
 if not found or not exists(select 1 from public.suppliers where id=p_supplier_id and client_id=p_client_id and is_active is not false) then return null; end if;
 if r.due_by < now() and not exists(select 1 from public.negotiation_sessions where rfq_id=p_rfq_id and supplier_id=p_supplier_id and client_id=p_client_id and status='active') then return null; end if;
 select * into a from public.commercial_action_intents where id=p_approval_id and client_id=p_client_id
 and supplier_id=p_supplier_id and rfq_id=p_rfq_id and quote_id=p_quote_id and outcome='APPROVE'
 and action_type in ('accept','confirm','award','accept_quote') and acceptance_consumed_at is null
 and created_at > now()-interval '10 minutes' for update;
 if not found or (a.guard_snapshot->'current_offer') is distinct from p_offer then return null; end if;
 select * into q from public.quotes where id=p_quote_id and rfq_id=p_rfq_id and supplier_id=p_supplier_id and is_available=true;
 if not found or q.price is null or q.price <> (a.proposal->>'price')::numeric then return null; end if;
 if link.commercial_state='revision_pending' and link.pending_revision_quote_id is distinct from p_quote_id then return null; end if;
 -- No stale acceptance if this variant (or an ambiguous unlabelled revision) has changed.
 if exists(select 1 from public.quotes n where n.rfq_id=p_rfq_id and n.supplier_id=p_supplier_id
 and (n.created_at,n.id) > (q.created_at,q.id)
 and (coalesce(lower(trim(n.variant_label)),'')='' or coalesce(lower(trim(n.variant_label)),'')=coalesce(lower(trim(p_offer->>'variant_label')),''))) then return null; end if;
 for src in select key,value from jsonb_each(p_offer->'term_sources') loop
   select * into source_row from public.quotes where id=(src.value->>'quote_id')::uuid and rfq_id=p_rfq_id and supplier_id=p_supplier_id;
   if not found or (to_jsonb(source_row)->src.key) is distinct from (src.value->'value') then return null; end if;
 end loop;
 cap=r.acceptable_price_max;
 if cap is null and r.acceptable_price_min is null and r.last_quote>0 then cap=r.last_quote+5; end if;
 select * into c from public.rfq_negotiation_constraints where rfq_id=p_rfq_id and dimension='price' and status='authorized' limit 1;
 if found and (c.constraints->>'max')::numeric>0 then cap=(c.constraints->>'max')::numeric; end if;
 if cap is null or q.price>cap or (r.last_quote>0 and q.price>=3*r.last_quote) then return null; end if;
 qty=(p_offer->>'quantity')::integer; days=(p_offer->>'resolved_delivery_days')::integer;
 if qty is null or qty<=0 or days is null or days<0
 or qty<>(a.proposal->>'quantity')::integer or days<>(a.proposal->>'delivery_days')::integer then return null; end if;
 if r.quantity is not null and qty<>r.quantity then
   select * into c from public.rfq_negotiation_constraints where rfq_id=p_rfq_id and dimension='quantity' and status='authorized' limit 1;
   if found then r.quantity_flexible=true; r.quantity_max=(c.constraints->>'max')::integer; end if;
   if not coalesce(r.quantity_flexible,false) or (r.quantity_max is not null and qty>r.quantity_max) then return null; end if;
   select * into c from public.rfq_negotiation_constraints where rfq_id=p_rfq_id and dimension='quantity' and status='authorized' limit 1;
   if found and ((c.constraints->>'min')::numeric>qty or (c.constraints->>'max')::numeric<qty) then return null; end if;
 end if;
 if r.required_delivery_days is not null and days>r.required_delivery_days then
   select * into c from public.rfq_negotiation_constraints where rfq_id=p_rfq_id and dimension='delivery' and status='authorized' limit 1;
   if found then r.delivery_flexible=true; r.max_delivery_days=(c.constraints->>'max_days')::integer; end if;
   if not coalesce(r.delivery_flexible,false) or (r.max_delivery_days is not null and days>r.max_delivery_days) then return null; end if;
 end if;
 selected=public.select_current_supplier_quote(p_client_id,p_supplier_id,p_rfq_id,p_quote_id,p_accepted_by);
 update public.commercial_action_intents set acceptance_consumed_at=now() where id=p_approval_id;
 update public.negotiation_sessions set status='accepted' where client_id=p_client_id and rfq_id=p_rfq_id and supplier_id=p_supplier_id and status='active';
 return selected;
end $$;
revoke all on function public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text) from public,anon,authenticated;
grant execute on function public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text) to service_role;

-- Atomically authorize exact approved messages, including multi-item concatenations.
create or replace function public.claim_commercial_message_approval(p_client_id uuid,p_supplier_id uuid,p_body text)
returns table(approval_id uuid,rfq_id uuid) language plpgsql security definer set search_path='' as $$
declare remaining text:=trim(p_body); a record; ids uuid[]:='{}'; candidate text; ambiguous boolean;
begin
 if remaining='' then return; end if;
 for a in select c.* from public.commercial_action_intents c join public.rfqs r on r.id=c.rfq_id
 join public.suppliers s on s.id=c.supplier_id
 where c.client_id=p_client_id and c.supplier_id=p_supplier_id and s.client_id=p_client_id and s.is_active is not false
 and c.outcome='APPROVE' and c.consumed_at is null and c.approved_message<>'' and c.created_at>now()-interval '10 minutes'
 and r.status='active' and (r.due_by>=now() or exists(select 1 from public.negotiation_sessions ns where ns.rfq_id=r.id and ns.supplier_id=p_supplier_id and ns.status='active'))
 and (c.action_type not in ('accept','confirm','award','accept_quote') or c.acceptance_consumed_at is not null)
 and (c.action_type not in ('counter','counteroffer','negotiate','accept','confirm','award','accept_quote') or
      (c.proposal->>'price')::numeric<=coalesce(r.acceptable_price_max,case when r.acceptable_price_min is null and r.last_quote>0 then r.last_quote+5 end))
 and (c.action_type not in ('counter','counteroffer','negotiate','accept','confirm','award','accept_quote') or exists(
     select 1 from public.quotes q where q.id=(c.guard_snapshot->'current_offer'->>'id')::uuid
     and q.rfq_id=c.rfq_id and q.supplier_id=c.supplier_id and q.is_available=true
     and q.price=(c.guard_snapshot->'current_offer'->>'price')::numeric
     and not exists(select 1 from public.quotes n where n.rfq_id=q.rfq_id and n.supplier_id=q.supplier_id
       and (n.created_at,n.id)>(q.created_at,q.id)
       and (coalesce(lower(trim(n.variant_label)),'')='' or coalesce(lower(trim(n.variant_label)),'')=coalesce(lower(trim(c.guard_snapshot->'current_offer'->>'variant_label')),'')))))
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
