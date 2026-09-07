-- Allow anon and authenticated users to read unexpired, unused invite tokens
create policy "invite_tokens_select_anon" on public.invite_tokens
  for select to anon, authenticated
  using (used_at is null and expires_at > now());

create policy "invite_tokens_update_anon" on public.invite_tokens
  for update to anon, authenticated
  using (used_at is null and expires_at > now())
  with check (used_at is not null);
