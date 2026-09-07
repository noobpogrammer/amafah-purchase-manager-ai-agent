-- Migration: Replace demo tenant policies with tenant-aware RLS using profiles
-- created_at: 2026-09-04

-- Drop demo policies if present
drop policy if exists demo_anon_clients on clients;
drop policy if exists demo_anon_suppliers on suppliers;
drop policy if exists demo_anon_rfqs on rfqs;
drop policy if exists demo_anon_pending_clarifications on pending_clarifications;
drop policy if exists demo_anon_message_log on message_log;
drop policy if exists demo_anon_flagged_for_review on flagged_for_review;
drop policy if exists demo_anon_rfq_suppliers on rfq_suppliers;
drop policy if exists demo_anon_quotes on quotes;
drop policy if exists demo_anon_rfq_rankings on rfq_rankings;

-- Helper note: tenant owner is resolved via profiles where profiles.id = auth.uid()

-- Clients: allow authenticated users to SELECT only their own client row (via profiles.client_id)
create policy "clients_select_tenant" on clients for select to authenticated using (
  exists (
    select 1 from public.profiles p where p.id = auth.uid() and p.client_id = clients.id
  )
);

-- Allow admins to UPDATE their client row (prevents ordinary members from changing tenant-level data)
create policy "clients_update_admins" on clients for update to authenticated using (
  exists (
    select 1 from public.profiles p where p.id = auth.uid() and p.role = 'admin' and p.client_id = clients.id
  )
) with check (
  exists (
    select 1 from public.profiles p where p.id = auth.uid() and p.role = 'admin' and p.client_id = clients.id
  )
);

-- Suppliers (have client_id column)
create policy "suppliers_select_tenant" on suppliers for select to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = suppliers.client_id)
);
create policy "suppliers_insert_tenant" on suppliers for insert to authenticated with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = suppliers.client_id)
);
create policy "suppliers_update_tenant" on suppliers for update to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = suppliers.client_id)
) with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = suppliers.client_id)
);
create policy "suppliers_delete_tenant" on suppliers for delete to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = suppliers.client_id)
);

-- RFQs (have client_id)
create policy "rfqs_select_tenant" on rfqs for select to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = rfqs.client_id)
);
create policy "rfqs_insert_tenant" on rfqs for insert to authenticated with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = rfqs.client_id)
);
create policy "rfqs_update_tenant" on rfqs for update to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = rfqs.client_id)
) with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = rfqs.client_id)
);
create policy "rfqs_delete_tenant" on rfqs for delete to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = rfqs.client_id)
);

-- RFQ Suppliers: parent relationship through rfqs
create policy "rfq_suppliers_select_tenant" on rfq_suppliers for select to authenticated using (
  exists (
    select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_suppliers.rfq_id and p.client_id = r.client_id
  )
);
create policy "rfq_suppliers_insert_tenant" on rfq_suppliers for insert to authenticated with check (
  exists (
    select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_suppliers.rfq_id and p.client_id = r.client_id
  )
);
create policy "rfq_suppliers_update_tenant" on rfq_suppliers for update to authenticated using (
  exists (
    select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_suppliers.rfq_id and p.client_id = r.client_id
  )
) with check (
  exists (
    select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_suppliers.rfq_id and p.client_id = r.client_id
  )
);
create policy "rfq_suppliers_delete_tenant" on rfq_suppliers for delete to authenticated using (
  exists (
    select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_suppliers.rfq_id and p.client_id = r.client_id
  )
);

-- Quotes: enforce via rfqs
create policy "quotes_select_tenant" on quotes for select to authenticated using (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = quotes.rfq_id and p.client_id = r.client_id)
);
create policy "quotes_insert_tenant" on quotes for insert to authenticated with check (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = quotes.rfq_id and p.client_id = r.client_id)
);
create policy "quotes_update_tenant" on quotes for update to authenticated using (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = quotes.rfq_id and p.client_id = r.client_id)
) with check (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = quotes.rfq_id and p.client_id = r.client_id)
);
create policy "quotes_delete_tenant" on quotes for delete to authenticated using (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = quotes.rfq_id and p.client_id = r.client_id)
);

-- Pending clarifications (have client_id)
create policy "pending_clarifications_select_tenant" on pending_clarifications for select to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = pending_clarifications.client_id)
);
create policy "pending_clarifications_insert_tenant" on pending_clarifications for insert to authenticated with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = pending_clarifications.client_id)
);
create policy "pending_clarifications_update_tenant" on pending_clarifications for update to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = pending_clarifications.client_id)
) with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = pending_clarifications.client_id)
);

-- Message log (have client_id)
create policy "message_log_select_tenant" on message_log for select to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = message_log.client_id)
);
create policy "message_log_insert_tenant" on message_log for insert to authenticated with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = message_log.client_id)
);

-- Flagged for review (have client_id)
create policy "flagged_select_tenant" on flagged_for_review for select to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = flagged_for_review.client_id)
);
create policy "flagged_insert_tenant" on flagged_for_review for insert to authenticated with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = flagged_for_review.client_id)
);
create policy "flagged_update_tenant" on flagged_for_review for update to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = flagged_for_review.client_id)
) with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = flagged_for_review.client_id)
);

-- RFQ rankings: enforce via rfqs
create policy "rfq_rankings_select_tenant" on rfq_rankings for select to authenticated using (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_rankings.rfq_id and p.client_id = r.client_id)
);
create policy "rfq_rankings_insert_tenant" on rfq_rankings for insert to authenticated with check (
  exists (select 1 from public.rfqs r join public.profiles p on p.id = auth.uid() where r.id = rfq_rankings.rfq_id and p.client_id = r.client_id)
);

-- Categories: tenant-scoped in this schema (has client_id). Enforce tenant policies.
alter table if exists categories enable row level security;
create policy "categories_select_tenant" on categories for select to authenticated using (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = categories.client_id)
);
create policy "categories_insert_tenant" on categories for insert to authenticated with check (
  exists (select 1 from public.profiles p where p.id = auth.uid() and p.client_id = categories.client_id)
);
