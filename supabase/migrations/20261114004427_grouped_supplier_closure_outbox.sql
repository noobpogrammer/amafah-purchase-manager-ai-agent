create table public.supplier_closure_outbox (
 id uuid primary key default gen_random_uuid(),
 client_id uuid not null references public.clients(id),
 supplier_id uuid not null references public.suppliers(id),
 rfq_id uuid not null references public.rfqs(id),
 status text not null default 'pending' check(status in ('pending','processing','sent','suppressed','unknown')),
 available_at timestamptz not null default now()+interval '90 seconds',
 batch_id uuid,
 message_log_id uuid references public.message_log(id),
 created_at timestamptz not null default now(),
 unique(rfq_id,supplier_id)
);
alter table public.supplier_closure_outbox enable row level security;
revoke all on public.supplier_closure_outbox from public,anon,authenticated;
grant all on public.supplier_closure_outbox to service_role;
create index supplier_closure_pending on public.supplier_closure_outbox(available_at) where status='pending';

create or replace function public.queue_supplier_closure(p_client_id uuid,p_supplier_id uuid,p_rfq_id uuid)
returns boolean language plpgsql security definer set search_path='' as $$
begin
 if not exists(select 1 from public.rfqs r join public.rfq_suppliers rs on rs.rfq_id=r.id
 join public.suppliers s on s.id=rs.supplier_id and s.client_id=r.client_id
 where r.id=p_rfq_id and r.client_id=p_client_id and r.status='closed' and rs.supplier_id=p_supplier_id
 and s.is_active is not false and s.deleted_at is null) then return false; end if;
 if exists(select 1 from public.quotes where rfq_id=p_rfq_id and supplier_id=p_supplier_id and is_current_accepted=true) then return true; end if;
 insert into public.supplier_closure_outbox(client_id,supplier_id,rfq_id)
 values(p_client_id,p_supplier_id,p_rfq_id) on conflict(rfq_id,supplier_id) do nothing;
 return true;
end $$;

create or replace function public.claim_supplier_closures(p_client_id uuid,p_supplier_id uuid)
returns setof public.supplier_closure_outbox language plpgsql security definer set search_path='' as $$
declare batch uuid:=gen_random_uuid();
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_client_id::text||p_supplier_id::text||':closure',0));
 if not exists(select 1 from public.supplier_closure_outbox where client_id=p_client_id and supplier_id=p_supplier_id and status='pending' and available_at<=now()) then return; end if;
 -- Collect adjacent endings, including newer rows within the collection window.
 return query update public.supplier_closure_outbox set status='processing',batch_id=batch
 where id in(select id from public.supplier_closure_outbox where client_id=p_client_id and supplier_id=p_supplier_id and status='pending' order by created_at limit 8 for update skip locked) returning *;
end $$;
revoke all on function public.queue_supplier_closure(uuid,uuid,uuid),public.claim_supplier_closures(uuid,uuid) from public,anon,authenticated;
grant execute on function public.queue_supplier_closure(uuid,uuid,uuid),public.claim_supplier_closures(uuid,uuid) to service_role;
