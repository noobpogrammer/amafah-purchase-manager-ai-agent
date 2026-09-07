-- Migration: Allow authenticated users to view team members sharing their client_id
-- created_at: 2026-09-07 19:05:32

-- 1. Helper function to retrieve the current user's client_id without triggering RLS recursion
create or replace function public.get_auth_user_client_id()
returns uuid
language sql
security definer
stable
set search_path = public
as $$
  select client_id from public.profiles where id = auth.uid() limit 1;
$$;

-- 2. Drop existing restrictive SELECT policy on profiles
drop policy if exists "profiles_select_own" on public.profiles;
drop policy if exists "profiles_select_tenant" on public.profiles;

-- 3. Create tenant-aware SELECT policy on profiles
create policy "profiles_select_tenant" on public.profiles
  for select
  to authenticated
  using (
    id = auth.uid()
    or (
      client_id is not null
      and client_id = public.get_auth_user_client_id()
    )
  );
