alter table public.pending_commercial_drafts
 add column if not exists clarification_approval_id uuid references public.commercial_action_intents(id),
 add column if not exists question_claimed_at timestamptz,
 add column if not exists answer_source_message_id uuid references public.message_log(id);

-- One supplier lock serializes creation and answering. Source messages are tenant-bound.
create or replace function public.prepare_rfq_clarification(
 p_client_id uuid,p_supplier_id uuid,p_source_id uuid,p_candidates uuid[],p_facts jsonb,
 p_issue_question boolean default true
) returns jsonb language plpgsql security definer set search_path='' as $$
declare d public.pending_commercial_drafts%rowtype; m public.message_log%rowtype;
 a public.commercial_action_intents%rowtype; ids uuid[]; outstanding boolean;
 question text:='Could you please confirm which product your message refers to?';
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_client_id::text||p_supplier_id::text,0));
 select * into m from public.message_log where id=p_source_id and client_id=p_client_id and supplier_id=p_supplier_id and direction='inbound';
 if not found then raise exception 'A saved supplier inbound source is required'; end if;
 if not exists(select 1 from public.suppliers where id=p_supplier_id and client_id=p_client_id and is_active is not false and deleted_at is null) then raise exception 'Supplier unavailable'; end if;
 select array_agg(distinct r.id) into ids from public.rfqs r join public.rfq_suppliers rs on rs.rfq_id=r.id
 where r.client_id=p_client_id and rs.supplier_id=p_supplier_id and r.id=any(p_candidates) and r.status='active';
 if ids is null then raise exception 'No eligible linked candidates'; end if;
 select * into d from public.pending_commercial_drafts where client_id=p_client_id and supplier_id=p_supplier_id and source_message_id=p_source_id::text order by created_at limit 1 for update;
 if found and d.status<>'awaiting_rfq_resolution' then
  return jsonb_build_object('outcome','ALREADY_HANDLED','draft',to_jsonb(d),'approved_message',null);
 end if;
 if d.id is null then
  insert into public.pending_commercial_drafts(client_id,supplier_id,source_message_id,raw_message,candidate_rfq_ids,extracted_facts,status)
  values(p_client_id,p_supplier_id,p_source_id::text,m.body,ids,coalesce(p_facts,'{}'),'awaiting_rfq_resolution') returning * into d;
 end if;
 -- Immutable captured evidence: retries cannot overwrite an existing draft's facts.
 if not p_issue_question then return jsonb_build_object('saved',true,'draft',to_jsonb(d)); end if;
 if d.question_claimed_at is not null then
  return jsonb_build_object('outcome','ALREADY_PENDING','draft',to_jsonb(d),'approved_message',null);
 end if;
 -- Do not send an indistinguishable second question for another supplier message.
 select exists(select 1 from public.pending_commercial_drafts x where x.client_id=p_client_id and x.supplier_id=p_supplier_id and x.status='awaiting_rfq_resolution' and x.id<>d.id and x.question_claimed_at is not null) into outstanding;
 if outstanding then return jsonb_build_object('outcome','ALREADY_PENDING','draft',to_jsonb(d),'approved_message',null); end if;
 if d.clarification_approval_id is not null then
  select * into a from public.commercial_action_intents where id=d.clarification_approval_id;
  -- Never silently renew an expired or uncertain send approval.
  if a.consumed_at is not null or a.created_at<now()-interval '10 minutes' then
   return jsonb_build_object('outcome','ALREADY_PENDING','draft',to_jsonb(d),'approved_message',null);
  end if;
 else
  insert into public.commercial_action_intents(client_id,supplier_id,action_type,attempt_number,proposal,guard_snapshot,outcome,approved_message)
  values(p_client_id,p_supplier_id,'clarify_rfq',1,'{}',jsonb_build_object('candidate_rfq_ids',to_jsonb(ids),'draft_id',d.id),'APPROVE',question) returning * into a;
  update public.pending_commercial_drafts set clarification_approval_id=a.id,updated_at=now() where id=d.id returning * into d;
 end if;
 return jsonb_build_object('outcome','APPROVE','approved_message',question,'draft',to_jsonb(d),'changes_commercial_records',false);
end $$;

create or replace function public.claim_rfq_clarification(p_client_id uuid,p_supplier_id uuid,p_body text)
returns uuid language plpgsql security definer set search_path='' as $$
declare a uuid; d uuid;
begin
 if p_body<>'Could you please confirm which product your message refers to?' then return null; end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_client_id::text||p_supplier_id::text,0));
 if exists(select 1 from public.pending_commercial_drafts where client_id=p_client_id and supplier_id=p_supplier_id and status='awaiting_rfq_resolution' and question_claimed_at is not null) then return null; end if;
 select c.id,x.id into a,d from public.commercial_action_intents c
 join public.pending_commercial_drafts x on x.clarification_approval_id=c.id and x.client_id=c.client_id and x.supplier_id=c.supplier_id
 join public.suppliers s on s.id=c.supplier_id and s.client_id=c.client_id
 where c.client_id=p_client_id and c.supplier_id=p_supplier_id and s.is_active is not false and s.deleted_at is null
 and c.action_type='clarify_rfq' and c.outcome='APPROVE' and c.rfq_id is null and c.quote_id is null
 and c.proposal='{}'::jsonb and c.approved_message=p_body and c.consumed_at is null and c.created_at>now()-interval '10 minutes'
 and x.status='awaiting_rfq_resolution' and x.question_claimed_at is null
 and exists(select 1 from public.rfq_suppliers rs join public.rfqs r on r.id=rs.rfq_id where rs.supplier_id=p_supplier_id and r.client_id=p_client_id and r.status='active' and r.id=any(x.candidate_rfq_ids))
 order by c.created_at limit 1 for update of c,x;
 if a is null then return null; end if;
 update public.commercial_action_intents set consumed_at=now() where id=a;
 update public.pending_commercial_drafts set question_claimed_at=now(),updated_at=now() where id=d;
 return a;
end $$;

create or replace function public.resolve_rfq_clarification(p_client_id uuid,p_supplier_id uuid,p_draft_id uuid,p_rfq_id uuid,p_answer_id uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
declare d public.pending_commercial_drafts%rowtype; m public.message_log%rowtype;
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_client_id::text||p_supplier_id::text,0));
 select * into d from public.pending_commercial_drafts where id=p_draft_id and client_id=p_client_id and supplier_id=p_supplier_id for update;
 if not found then return jsonb_build_object('conflict',true,'reason','Draft missing'); end if;
 if d.status='resolved' then
  if d.resolved_rfq_id=p_rfq_id and d.answer_source_message_id=p_answer_id then return jsonb_build_object('resolved',true,'draft',to_jsonb(d),'already_resolved',true); end if;
  return jsonb_build_object('conflict',true,'reason','Draft already resolved differently');
 end if;
 if d.status<>'awaiting_rfq_resolution' or not(p_rfq_id=any(d.candidate_rfq_ids)) then return jsonb_build_object('conflict',true,'reason','Draft not waiting or RFQ outside candidates'); end if;
 select * into m from public.message_log where id=p_answer_id and client_id=p_client_id and supplier_id=p_supplier_id and direction='inbound';
 if not found or p_answer_id::text=d.source_message_id or m.created_at<d.created_at then return jsonb_build_object('conflict',true,'reason','A subsequent supplier answer is required'); end if;
 if not exists(select 1 from public.rfqs r join public.rfq_suppliers rs on rs.rfq_id=r.id where r.id=p_rfq_id and r.client_id=p_client_id and rs.supplier_id=p_supplier_id and r.status='active') then return jsonb_build_object('conflict',true,'reason','RFQ no longer active or linked'); end if;
 update public.pending_commercial_drafts set status='resolved',resolved_rfq_id=p_rfq_id,answer_source_message_id=p_answer_id,resolved_at=now(),updated_at=now() where id=d.id returning * into d;
 return jsonb_build_object('resolved',true,'draft',to_jsonb(d));
end $$;
revoke all on function public.prepare_rfq_clarification(uuid,uuid,uuid,uuid[],jsonb,boolean),public.resolve_rfq_clarification(uuid,uuid,uuid,uuid,uuid) from public,anon,authenticated;
grant execute on function public.prepare_rfq_clarification(uuid,uuid,uuid,uuid[],jsonb,boolean),public.resolve_rfq_clarification(uuid,uuid,uuid,uuid,uuid) to service_role;

-- Existing accepted deals remain discussable after RFQ expiry/closure; no new commitment.
do $migration$
declare definition text; updated text;
begin
 select pg_get_functiondef('public.claim_commercial_message_approval(uuid,uuid,text)'::regprocedure) into definition;
 updated:=replace(definition,
 '(c.action_type=''system_notice'' and c.guard_snapshot->>''issued_by''=''backend'')',
 '(c.action_type=''system_notice'' and c.guard_snapshot->>''issued_by''=''backend'') or
 (c.action_type in (''acknowledge'',''acknowledgement'') and c.proposal=''{}''::jsonb and exists(select 1 from public.quotes accepted where accepted.id=(c.guard_snapshot->''current_offer''->>''id'')::uuid and accepted.rfq_id=c.rfq_id and accepted.supplier_id=c.supplier_id and accepted.is_current_accepted=true and accepted.price=(c.guard_snapshot->''current_offer''->>''price'')::numeric))');
 if updated=definition then raise exception 'Expected send eligibility predicate missing'; end if;
 execute updated;
end $migration$;
