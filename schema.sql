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
    whatsapp_instance   text not null unique,   -- Evolution API instance name
    timezone            text not null default 'Asia/Dubai',
    is_active           boolean not null default true,
    created_at          timestamptz not null default now()
);

-- ------------------------------------------------------------
-- SUPPLIERS
-- A supplier belongs to exactly one client. Phone is stored
-- normalized (E.164-ish, "+971 50 1234567" style) so matching
-- incoming WhatsApp messages is a simple lookup, not fuzzy logic.
-- ------------------------------------------------------------
create table suppliers (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    name                text not null,
    phone_number        text not null,          -- normalized, e.g. "+971501234567"
    category            text[],                  -- array of categories, e.g. {'Electronics', 'Hardware'}
    notes               text,
    is_active           boolean not null default true,
    created_at          timestamptz not null default now(),

    unique (client_id, phone_number)
);

create index idx_suppliers_client on suppliers(client_id);
create index idx_suppliers_phone on suppliers(phone_number);

-- ------------------------------------------------------------
-- RFQS (Request for Quote)
-- One RFQ = one product ask that may go out to multiple suppliers.
-- ------------------------------------------------------------
create table rfqs (
    id                  uuid primary key default gen_random_uuid(),
    client_id           uuid not null references clients(id) on delete cascade,
    product_name        text not null,
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
-- One row per supplier's actual quote for an RFQ. Unlike the old
-- Sheet, a supplier can have a clean, structured quote history —
-- no cramming multiple values into a single cell.
-- ------------------------------------------------------------
create table quotes (
    id                  uuid primary key default gen_random_uuid(),
    rfq_id              uuid not null references rfqs(id) on delete cascade,
    supplier_id         uuid not null references suppliers(id) on delete cascade,
    price               numeric(12, 2),
    delivery_time       text,
    quality_notes       text,
    raw_message         text,                    -- original WhatsApp text, for auditing/debugging
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
    pending_rfq_ids     uuid[] not null,          -- the candidate RFQs it could be
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
-- Stores the AI-generated comparison report per RFQ, so it's
-- retrievable later without re-running the LLM.
-- ------------------------------------------------------------
create table rfq_rankings (
    id                  uuid primary key default gen_random_uuid(),
    rfq_id              uuid not null references rfqs(id) on delete cascade,
    best_supplier_id    uuid references suppliers(id),
    reasoning           text,
    ranking_json        jsonb not null,           -- full ranked list, structured
    created_at          timestamptz not null default now()
);

create index idx_rfq_rankings_rfq on rfq_rankings(rfq_id);
