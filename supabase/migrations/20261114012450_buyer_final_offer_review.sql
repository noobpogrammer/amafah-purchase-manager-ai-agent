create table public.buyer_final_offer_reviews (
 id uuid primary key default gen_random_uuid(), client_id uuid not null references public.clients(id),
 supplier_id uuid not null references public.suppliers(id),rfq_id uuid not null references public.rfqs(id),
 quote_id uuid not null unique references public.quotes(id), offer_snapshot jsonb not null,
 status text not null default 'pending' check(status in ('pending','authorized','completed','stale')),
 decision text check(decision in ('accept','negotiate')),counter_price numeric,
 decided_by uuid,decided_at timestamptz,job_id uuid not null default gen_random_uuid(),
 job_state text check(job_state in ('queued','running','completed','failed','unknown')),
 job_error text,created_at timestamptz not null default now()
);
alter table public.buyer_final_offer_reviews enable row level security;
revoke all on public.buyer_final_offer_reviews from public,anon,authenticated;
grant all on public.buyer_final_offer_reviews to service_role;

create function public.queue_buyer_final_offer(p_client_id uuid,p_supplier_id uuid,p_rfq_id uuid,p_quote_id uuid,p_snapshot jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare review public.buyer_final_offer_reviews%rowtype;
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_client_id::text||p_rfq_id::text||p_supplier_id::text,0));
 if not exists(select 1 from public.quotes q join public.rfqs r on r.id=q.rfq_id join public.rfq_suppliers rs on rs.rfq_id=r.id and rs.supplier_id=q.supplier_id
 where q.id=p_quote_id and q.rfq_id=p_rfq_id and q.supplier_id=p_supplier_id and r.client_id=p_client_id and r.status='active' and q.is_available=true and q.price=(p_snapshot->>'price')::numeric) then raise exception 'Offer no longer eligible';end if;
 insert into public.buyer_final_offer_reviews(client_id,supplier_id,rfq_id,quote_id,offer_snapshot)
 values(p_client_id,p_supplier_id,p_rfq_id,p_quote_id,p_snapshot) on conflict(quote_id) do nothing;
 select * into review from public.buyer_final_offer_reviews where quote_id=p_quote_id and client_id=p_client_id and supplier_id=p_supplier_id;
 if review.status='pending' then
  update public.negotiation_sessions set status='awaiting_human_review',updated_at=now()
  where client_id=p_client_id and supplier_id=p_supplier_id and rfq_id=p_rfq_id and status in ('active','supplier_final','target_reached','awaiting_authorization');
  if not exists(select 1 from public.negotiation_sessions where client_id=p_client_id and supplier_id=p_supplier_id and rfq_id=p_rfq_id and status in ('active','awaiting_human_review','awaiting_authorization')) then
   insert into public.negotiation_sessions(client_id,supplier_id,rfq_id,status,latest_supplier_offer) values(p_client_id,p_supplier_id,p_rfq_id,'awaiting_human_review',(p_snapshot->>'price')::numeric);
  end if;
 end if;
 return to_jsonb(review);
end $$;

create function public.decide_buyer_final_offer(p_client_id uuid,p_review_id uuid,p_user_id uuid,p_decision text,p_counter numeric)
returns jsonb language plpgsql security definer set search_path='' as $$
declare review public.buyer_final_offer_reviews%rowtype;
begin
 select * into review from public.buyer_final_offer_reviews where id=p_review_id and client_id=p_client_id for update;
 if not found then raise exception 'Review not found';end if;
 if review.status='authorized' and review.decision=p_decision and review.counter_price is not distinct from p_counter then return to_jsonb(review);end if;
 if review.status<>'pending' then raise exception 'Review already decided or stale';end if;
 if p_decision not in ('accept','negotiate') or (p_decision='accept' and p_counter is not null) or (p_decision='negotiate' and p_counter is not null and (p_counter<=0 or p_counter>=(review.offer_snapshot->>'price')::numeric)) then raise exception 'Invalid decision';end if;
 if not exists(select 1 from public.profiles where id=p_user_id and client_id=p_client_id and role='admin') then raise exception 'Buyer administrator required';end if;
 update public.buyer_final_offer_reviews set status='authorized',decision=p_decision,counter_price=p_counter,decided_by=p_user_id,decided_at=now(),job_state='queued' where id=p_review_id returning * into review;
 return to_jsonb(review);
end $$;
revoke all on function public.queue_buyer_final_offer(uuid,uuid,uuid,uuid,jsonb),public.decide_buyer_final_offer(uuid,uuid,uuid,text,numeric) from public,anon,authenticated;
grant execute on function public.queue_buyer_final_offer(uuid,uuid,uuid,uuid,jsonb),public.decide_buyer_final_offer(uuid,uuid,uuid,text,numeric) to service_role;

-- Defence at actual acceptance, not just the model's review tool.
do $migration$
declare definition text;updated text;
begin
 select pg_get_functiondef('public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text)'::regprocedure) into definition;
 updated:=replace(definition,' selected=public.select_current_supplier_quote',
 ' if (p_offer->>''price'')::numeric>coalesce((select (pc.constraints->>''preferred_target'')::numeric from public.rfq_negotiation_constraints pc where pc.rfq_id=p_rfq_id and pc.dimension=''price'' and pc.status=''authorized'' limit 1),r.acceptable_price_min,r.last_quote) and exists(select 1 from public.buyer_final_offer_reviews br where br.quote_id=p_quote_id) and not exists(select 1 from public.buyer_final_offer_reviews br where br.quote_id=p_quote_id and br.client_id=p_client_id and br.supplier_id=p_supplier_id and br.status=''authorized'' and br.decision=''accept'' and br.offer_snapshot->>''price''=p_offer->>''price'' and br.offer_snapshot->>''quantity''=p_offer->>''quantity'' and br.offer_snapshot->>''resolved_delivery_days''=p_offer->>''resolved_delivery_days'' and br.offer_snapshot->>''quality_notes'' is not distinct from p_offer->>''quality_notes'') then return null;end if;
 selected=public.select_current_supplier_quote');
 if updated=definition then raise exception 'Acceptance hook missing';end if;
 updated:=replace(updated,' return selected;',' update public.buyer_final_offer_reviews set status=''completed'' where quote_id=p_quote_id and status in (''pending'',''authorized''); return selected;');
 execute updated;
end $migration$;
