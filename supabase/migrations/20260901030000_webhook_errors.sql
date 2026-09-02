-- ============================================================
-- Migration: Add webhook_errors table for persistent traceback logging
-- ============================================================

create table if not exists webhook_errors (
    id            uuid primary key default gen_random_uuid(),
    error_message text,
    traceback     text,
    raw_payload   jsonb,
    created_at    timestamptz not null default now()
);

alter table webhook_errors enable row level security;

drop policy if exists demo_anon_webhook_errors on webhook_errors;
create policy demo_anon_webhook_errors on webhook_errors for all to anon using (true) with check (true);
