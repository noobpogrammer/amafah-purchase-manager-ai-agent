-- ============================================================
-- Initial Schema Migration
-- ============================================================

-- Enable pgcrypto for UUID generation if needed
create extension if not exists "pgcrypto";

-- Clients (Tenant Stores / Hardware Shops)
create table if not exists clients (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    created_at  timestamptz not null default now()
);

-- Suppliers
create table if not exists suppliers (
    id           uuid primary key default gen_random_uuid(),
    client_id    uuid not null references clients(id) on delete cascade,
    name         text not null,
    phone_number text not null,
    category     text[],
    notes        text,
    created_at   timestamptz not null default now()
);

-- RFQs (Requests for Quotes)
create table if not exists rfqs (
    id             uuid primary key default gen_random_uuid(),
    client_id      uuid not null references clients(id) on delete cascade,
    product_name   text not null,
    category       text,
    specs          text,
    quantity       text,
    deadline_hours integer not null default 24,
    status         text not null default 'active'
                       check (status in ('active', 'closed', 'cancelled')),
    created_at     timestamptz not null default now()
);

-- RFQ <-> Supplier Join Table
create table if not exists rfq_suppliers (
    id               uuid primary key default gen_random_uuid(),
    rfq_id           uuid not null references rfqs(id) on delete cascade,
    supplier_id      uuid not null references suppliers(id) on delete cascade,
    status           text not null default 'sent'
                         check (status in ('sent', 'clarifying', 'responded', 'no_response')),
    sent_at          timestamptz not null default now(),
    reminder_count   integer not null default 0,
    last_reminder_at timestamptz,
    unique(rfq_id, supplier_id)
);

-- Recorded Quotes
create table if not exists quotes (
    id            uuid primary key default gen_random_uuid(),
    rfq_id        uuid not null references rfqs(id) on delete cascade,
    supplier_id   uuid not null references suppliers(id) on delete cascade,
    price         numeric(12, 2) not null,
    delivery_time text,
    quality_notes text,
    raw_message   text,
    confidence    text not null default 'high'
                      check (confidence in ('high', 'medium', 'low')),
    created_at    timestamptz not null default now()
);

-- Pending Clarifications
create table if not exists pending_clarifications (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    supplier_id         uuid not null references suppliers(id) on delete cascade,
    pending_rfq_ids     uuid[] not null,
    raw_message         text not null,
    extracted_price     numeric(12, 2),
    extracted_delivery  text,
    extracted_notes     text,
    status              text not null default 'awaiting_reply'
                            check (status in ('awaiting_reply', 'resolved', 'abandoned')),
    created_at          timestamptz not null default now(),
    resolved_at         timestamptz
);

-- Complete Message Audit Log
create table if not exists message_log (
    id          uuid primary key default gen_random_uuid(),
    client_id   uuid not null references clients(id) on delete cascade,
    supplier_id uuid not null references suppliers(id) on delete cascade,
    direction   text not null check (direction in ('inbound', 'outbound')),
    body        text not null,
    created_at  timestamptz not null default now()
);

-- Human Review Flag Queue
create table if not exists flagged_for_review (
    id          uuid primary key default gen_random_uuid(),
    client_id   uuid not null references clients(id) on delete cascade,
    supplier_id uuid not null references suppliers(id) on delete cascade,
    rfq_id      uuid references rfqs(id) on delete set null,
    reason      text not null,
    category    text not null check (category in ('requires_business_knowledge', 'unclear_intent', 'contradictory_information', 'other')),
    raw_message text,
    status      text not null default 'pending' check (status in ('pending', 'resolved', 'dismissed')),
    created_at  timestamptz not null default now(),
    resolved_at timestamptz
);

-- AI Quote Rankings
create table if not exists rfq_rankings (
    id            uuid primary key default gen_random_uuid(),
    rfq_id        uuid not null references rfqs(id) on delete cascade,
    rankings_json jsonb not null,
    summary       text,
    created_at    timestamptz not null default now()
);

-- Enable RLS
alter table clients enable row level security;
alter table suppliers enable row level security;
alter table rfqs enable row level security;
alter table rfq_suppliers enable row level security;
alter table quotes enable row level security;
alter table pending_clarifications enable row level security;
alter table message_log enable row level security;
alter table flagged_for_review enable row level security;
alter table rfq_rankings enable row level security;

-- Demo Permissive Policies
drop policy if exists demo_anon_clients on clients;
create policy demo_anon_clients on clients for all to anon using (id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b') with check (id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

drop policy if exists demo_anon_suppliers on suppliers;
create policy demo_anon_suppliers on suppliers for all to anon using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b') with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

drop policy if exists demo_anon_rfqs on rfqs;
create policy demo_anon_rfqs on rfqs for all to anon using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b') with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

drop policy if exists demo_anon_pending_clarifications on pending_clarifications;
create policy demo_anon_pending_clarifications on pending_clarifications for all to anon using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b') with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

drop policy if exists demo_anon_message_log on message_log;
create policy demo_anon_message_log on message_log for all to anon using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b') with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

drop policy if exists demo_anon_flagged_for_review on flagged_for_review;
create policy demo_anon_flagged_for_review on flagged_for_review for all to anon using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b') with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

drop policy if exists demo_anon_rfq_suppliers on rfq_suppliers;
create policy demo_anon_rfq_suppliers on rfq_suppliers for all to anon using (exists (select 1 from rfqs where rfqs.id = rfq_suppliers.rfq_id and rfqs.client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'));

drop policy if exists demo_anon_quotes on quotes;
create policy demo_anon_quotes on quotes for all to anon using (exists (select 1 from rfqs where rfqs.id = quotes.rfq_id and rfqs.client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'));

drop policy if exists demo_anon_rfq_rankings on rfq_rankings;
create policy demo_anon_rfq_rankings on rfq_rankings for all to anon using (exists (select 1 from rfqs where rfqs.id = rfq_rankings.rfq_id and rfqs.client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'));
