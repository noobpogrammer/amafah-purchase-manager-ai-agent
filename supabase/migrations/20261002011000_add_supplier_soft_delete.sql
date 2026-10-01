-- Admin supplier deletion is implemented as a soft delete so procurement
-- history (quotes, RFQs, messages, decisions) is preserved.
alter table public.suppliers
  add column if not exists deleted_at timestamptz;

create index if not exists idx_suppliers_client_deleted
  on public.suppliers (client_id, deleted_at);
