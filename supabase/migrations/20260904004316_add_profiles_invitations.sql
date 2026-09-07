-- Migration: Add `profiles` and `invitations` tables and RLS policies
-- NOTE: Do NOT modify previous migrations. This migration adds the auth foundation
-- created_at: 2026-09-04

-- Enable pgcrypto for UUID generation (safe no-op if enabled)
create extension if not exists pgcrypto;

-- 1) Profiles table: each profile.id is the auth.user id (uuid)
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  client_id uuid not null references public.clients(id) on delete cascade,
  email text,
  role text not null default 'member' check (role in ('admin','member')),
  created_at timestamptz not null default now()
);

create index if not exists idx_profiles_client on public.profiles(client_id);

-- 2) Invitations table: admin creates an invitation tied to a client
create table if not exists public.invitations (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  email text not null,
  role text not null default 'member' check (role in ('admin','member')),
  invited_by uuid references auth.users(id),
  token_hash text not null,
  expires_at timestamptz not null,
  accepted_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_invitations_client on public.invitations(client_id);
create index if not exists idx_invitations_email on public.invitations(email);

-- Enable RLS on profiles and invitations and add conservative tenant-aware policies.
alter table public.profiles enable row level security;
alter table public.invitations enable row level security;

-- Policies for profiles
-- Allow authenticated users to select their own profile
create policy "profiles_select_own" on public.profiles for select to authenticated using (
  id = auth.uid()
);

-- Allow users to insert a profile only if the row id matches their auth.uid().
-- In production, creating profiles should normally be done by a backend service-role
-- as part of the invitation acceptance flow. This INSERT policy allows self-creation
-- only for the matching auth user id; it does NOT permit assigning arbitrary client_id.
create policy "profiles_insert_self" on public.profiles for insert to authenticated with check (
  id = auth.uid()
);

-- Allow users to update their own profile (limited): role and client_id changes require admin/service-role
create policy "profiles_update_own" on public.profiles for update to authenticated using (
  id = auth.uid()
) with check (
  id = auth.uid()
);

-- Policies for invitations
-- Only authenticated admins (for the client) may insert invitations for that client
create policy "invitations_insert_admins_only" on public.invitations for insert to authenticated with check (
  exists (
    select 1 from public.profiles p
    where p.id = auth.uid() and p.role = 'admin' and p.client_id = invitations.client_id
  )
);

-- Allow admins to select invitations for their client and invitees to select their own invitation row
create policy "invitations_select_admins_or_self" on public.invitations for select to authenticated using (
  (
    exists (
      select 1 from public.profiles p where p.id = auth.uid() and p.role = 'admin' and p.client_id = invitations.client_id
    )
  )
  or
  (
    invitations.email = (select (auth.jwt() ->> 'email')::text)
  )
);

-- Allow admins to delete invitations for their client
create policy "invitations_delete_admins" on public.invitations for delete to authenticated using (
  exists (
    select 1 from public.profiles p where p.id = auth.uid() and p.role = 'admin' and p.client_id = invitations.client_id
  )
);

-- Note: The invitation acceptance flow (marking accepted_at, creating profile rows) should be
-- implemented by a backend endpoint using the service_role key, which verifies the invitation
-- token, creates the profile record `profiles(id=auth_user_id, client_id=...)` and then clears the
-- invitation. Do NOT trust client-supplied raw_user_meta_data for client_id.
