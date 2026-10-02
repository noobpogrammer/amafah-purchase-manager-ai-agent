alter table public.clients
  add column if not exists whatsapp_instance_aliases text[] not null default '{}'::text[];

create index if not exists idx_clients_whatsapp_instance_aliases
  on public.clients using gin (whatsapp_instance_aliases);
