alter table public.llm_usage_log
    add column if not exists execution_context text,
    add column if not exists test_name text;

update public.llm_usage_log
set execution_context = coalesce(execution_context, environment, 'unknown')
where execution_context is null;

alter table public.llm_usage_log
    alter column execution_context set not null;

create index if not exists idx_llm_usage_log_execution_context_created_at
    on public.llm_usage_log (execution_context, created_at desc);

create index if not exists idx_llm_usage_log_test_name_created_at
    on public.llm_usage_log (test_name, created_at desc);

comment on column public.llm_usage_log.execution_context is
'High-level LLM call origin such as production_whatsapp, pytest, ci, or manual_dev.';

comment on column public.llm_usage_log.test_name is
'Pytest node id when the call was made from pytest; null otherwise.';
