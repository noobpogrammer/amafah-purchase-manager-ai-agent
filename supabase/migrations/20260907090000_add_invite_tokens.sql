-- Migration: Add `invite_tokens` table for opaque shareable team invitations
-- created_at: 2026-09-07

create table if not exists public.invite_tokens (
    id uuid primary key default gen_random_uuid(),
    token text not null unique,
    client_id uuid not null references public.clients(id) on delete cascade,
    role text not null default 'member' check (role in ('admin', 'member')),
    created_by uuid references auth.users(id),
    used_at timestamptz,
    expires_at timestamptz not null default (now() + interval '7 days'),
    created_at timestamptz not null default now()
);

create index if not exists idx_invite_tokens_token on public.invite_tokens(token);
create index if not exists idx_invite_tokens_client_id on public.invite_tokens(client_id);

alter table public.invite_tokens enable row level security;
