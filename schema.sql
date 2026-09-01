-- ============================================================
-- Amafha Procurement Automation — Core Schema
-- Multi-tenant from day one: every business-data table carries
-- client_id so onboarding a new client never requires a migration.
-- ============================================================

create extension if not exists "pgcrypto"; -- for gen_random_uuid()

-- ------------------------------------------------------------
-- CLIENTS
-- One row per business using the system (e.g. the Dubai hardware
-- supermarket). Each client has its own WhatsApp (Evolution API)
-- instance so conversations never cross between businesses.
-- ------------------------------------------------------------
create table clients (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null,
    whatsapp_instance   text not null unique,
    timezone            text not null default 'Asia/Dubai',
    is_active           boolean not null default true,
    created_at          timestamptz not null default now()
);

-- ------------------------------------------------------------
-- SUPPLIERS
-- A supplier belongs to exactly one client. Phone is stored
-- normalized (E.164-ish, "+971 50 1234567" style) so matching
-- incoming WhatsApp messages is a simple lookup, not fuzzy logic.
-- category is an array because one supplier can serve multiple
-- categories, e.g. Electronics + Hardware.
-- ------------------------------------------------------------
create table suppliers (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    name                text not null,
    phone_number        text not null,
    category            text[],
    notes               text,
    is_active            boolean not null default true,
    created_at          timestamptz not null default now(),

    unique (client_id, phone_number)
);

create index idx_suppliers_client on suppliers(client_id);
create index idx_suppliers_phone on suppliers(phone_number);

-- ------------------------------------------------------------
-- RFQS (Request for Quote)
-- One RFQ = one product ask that may go out to all active suppliers
-- belonging to the selected category.
-- ------------------------------------------------------------
create table rfqs (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    product_name        text not null,
    category            text not null,
    specs               text,
    quantity            integer,
    status              text not null default 'active'
                            check (status in ('active', 'closed', 'cancelled')),
    deadline_hours      integer,
    due_by              timestamptz,
    created_at          timestamptz not null default now()
);

create index idx_rfqs_client on rfqs(client_id);
create index idx_rfqs_status on rfqs(status);
create index idx_rfqs_category on rfqs(category);

-- ------------------------------------------------------------
-- RFQ_SUPPLIERS (join table)
-- Tracks which suppliers a given RFQ was sent to, and per-supplier
-- lifecycle status — this is what "who hasn't responded yet" queries
-- against, replacing the old Sheets row-scanning logic.
-- ------------------------------------------------------------
create table rfq_suppliers (
    id                  uuid primary key default gen_random_uuid(),
    rfq_id              uuid not null references rfqs(id) on delete cascade,
    supplier_id         uuid not null references suppliers(id) on delete cascade,
    status              text not null default 'sent'
                            check (status in ('sent', 'responded', 'clarifying', 'no_response')),
    sent_at             timestamptz not null default now(),
    reminder_count      integer not null default 0,
    last_reminder_at    timestamptz,

    unique (rfq_id, supplier_id)
);

create index idx_rfq_suppliers_rfq on rfq_suppliers(rfq_id);
create index idx_rfq_suppliers_supplier on rfq_suppliers(supplier_id);
create index idx_rfq_suppliers_status on rfq_suppliers(status);

-- ------------------------------------------------------------
-- QUOTES
-- One row per supplier's actual quote for an RFQ.
-- ------------------------------------------------------------
create table quotes (
    id                  uuid primary key default gen_random_uuid(),
    rfq_id              uuid not null references rfqs(id) on delete cascade,
    supplier_id         uuid not null references suppliers(id) on delete cascade,
    price               numeric(12, 2),
    delivery_time       text,
    quality_notes       text,
    raw_message         text,
    confidence          text check (confidence in ('high', 'low')),
    created_at          timestamptz not null default now()
);

create index idx_quotes_rfq on quotes(rfq_id);
create index idx_quotes_supplier on quotes(supplier_id);

-- ------------------------------------------------------------
-- PENDING_CLARIFICATIONS
-- When the agent can't confidently match a reply to one RFQ
-- (e.g. supplier has 2+ open RFQs and the reply is ambiguous),
-- we park it here and ask a follow-up question instead of guessing.
-- ------------------------------------------------------------
create table pending_clarifications (
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

create index idx_pending_clarifications_client on pending_clarifications(client_id);
create index idx_pending_clarifications_supplier on pending_clarifications(supplier_id);
create index idx_pending_clarifications_status on pending_clarifications(status);

-- ------------------------------------------------------------
-- MESSAGE_LOG
-- Every inbound/outbound WhatsApp message, for auditing and for
-- giving the agent conversational memory/context if needed later.
-- ------------------------------------------------------------
create table message_log (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    supplier_id         uuid references suppliers(id) on delete set null,
    direction           text not null check (direction in ('inbound', 'outbound')),
    body                text not null,
    related_rfq_id      uuid references rfqs(id) on delete set null,
    created_at          timestamptz not null default now()
);

create index idx_message_log_client on message_log(client_id);
create index idx_message_log_supplier on message_log(supplier_id);

-- ------------------------------------------------------------
-- RFQ_RANKINGS
-- Stores the AI-generated comparison report per RFQ.
-- ------------------------------------------------------------
create table rfq_rankings (
    id                  uuid primary key default gen_random_uuid(),
    rfq_id              uuid not null references rfqs(id) on delete cascade,
    best_supplier_id    uuid references suppliers(id),
    reasoning           text,
    ranking_json        jsonb not null,
    created_at          timestamptz not null default now()
);

create index idx_rfq_rankings_rfq on rfq_rankings(rfq_id);

-- ------------------------------------------------------------
-- FLAGGED_FOR_REVIEW
-- Stores conversations flagged for human procurement manager review.
-- ------------------------------------------------------------
create table flagged_for_review (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    supplier_id         uuid not null references suppliers(id) on delete cascade,
    rfq_id              uuid references rfqs(id) on delete set null,
    reason              text not null,
    category            text not null check (category in ('requires_business_knowledge', 'unclear_intent', 'contradictory_information', 'other')),
    raw_message         text not null,
    status              text not null default 'pending' check (status in ('pending', 'resolved')),
    created_at          timestamptz not null default now(),
    resolved_at         timestamptz
);

create index idx_flagged_client on flagged_for_review(client_id);
create index idx_flagged_supplier on flagged_for_review(supplier_id);
create index idx_flagged_status on flagged_for_review(status);

-- ------------------------------------------------------------
-- ROW LEVEL SECURITY (RLS) & DEMO-SCOPED POLICIES
-- Enable RLS across all business tables and add permissive-but-scoped
-- policies for the public 'anon' role restricted to DEMO_CLIENT_ID.
-- NOTE: Replace with auth.uid() token checks prior to production launch.
-- ------------------------------------------------------------

alter table clients enable row level security;
alter table suppliers enable row level security;
alter table rfqs enable row level security;
alter table rfq_suppliers enable row level security;
alter table quotes enable row level security;
alter table pending_clarifications enable row level security;
alter table message_log enable row level security;
alter table rfq_rankings enable row level security;
alter table flagged_for_review enable row level security;

-- Demo client policies for tables with direct client_id column
create policy demo_anon_clients on clients for all to anon
  using (id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

create policy demo_anon_suppliers on suppliers for all to anon
  using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b')
  with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

create policy demo_anon_rfqs on rfqs for all to anon
  using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b')
  with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

create policy demo_anon_pending_clarifications on pending_clarifications for all to anon
  using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b')
  with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

create policy demo_anon_message_log on message_log for all to anon
  using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b')
  with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

create policy demo_anon_flagged_for_review on flagged_for_review for all to anon
  using (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b')
  with check (client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b');

-- Subquery policies for child tables without direct client_id column
create policy demo_anon_rfq_suppliers on rfq_suppliers for all to anon
  using (exists (select 1 from rfqs where rfqs.id = rfq_suppliers.rfq_id and rfqs.client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'));

create policy demo_anon_quotes on quotes for all to anon
  using (exists (select 1 from rfqs where rfqs.id = quotes.rfq_id and rfqs.client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'));

create policy demo_anon_rfq_rankings on rfq_rankings for all to anon
  using (exists (select 1 from rfqs where rfqs.id = rfq_rankings.rfq_id and rfqs.client_id = 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'));
