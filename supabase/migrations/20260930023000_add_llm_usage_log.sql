create table if not exists public.llm_usage_log (
    id uuid primary key default gen_random_uuid(),
    provider text not null,
    model text not null,
    call_type text not null,
    environment text not null,
    success boolean not null default true,
    input_tokens integer not null default 0 check (input_tokens >= 0),
    output_tokens integer not null default 0 check (output_tokens >= 0),
    total_tokens integer not null default 0 check (total_tokens >= 0),
    latency_ms integer not null default 0 check (latency_ms >= 0),
    request_id text,
    client_id uuid references public.clients(id) on delete set null,
    supplier_id uuid references public.suppliers(id) on delete set null,
    rfq_id uuid references public.rfqs(id) on delete set null,
    error_type text,
    created_at timestamptz not null default now()
);

create index if not exists idx_llm_usage_log_created_at
    on public.llm_usage_log (created_at desc);

create index if not exists idx_llm_usage_log_call_type_created_at
    on public.llm_usage_log (call_type, created_at desc);

create index if not exists idx_llm_usage_log_environment_created_at
    on public.llm_usage_log (environment, created_at desc);

create index if not exists idx_llm_usage_log_rfq_created_at
    on public.llm_usage_log (rfq_id, created_at desc);

alter table public.llm_usage_log enable row level security;

revoke all on table public.llm_usage_log from anon, authenticated;
grant all on table public.llm_usage_log to service_role;

comment on table public.llm_usage_log is
'Per-call LLM usage telemetry. Stores token counts and operational metadata only; never prompts, outputs, API keys, or hidden reasoning.';
