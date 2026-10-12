-- Read-only tenant visibility for bundled RFQ delivery in the buyer frontend.
-- Writes stay service-role-only. Never use user-editable JWT metadata for auth.
grant select on public.supplier_message_bundles to authenticated;
grant select on public.supplier_message_bundle_items to authenticated;

drop policy if exists supplier_message_bundles_tenant_read on public.supplier_message_bundles;
create policy supplier_message_bundles_tenant_read
on public.supplier_message_bundles
for select to authenticated
using (
  exists (
    select 1 from public.profiles p
    where p.id=(select auth.uid()) and p.client_id=supplier_message_bundles.client_id
  )
);

drop policy if exists supplier_message_bundle_items_tenant_read on public.supplier_message_bundle_items;
create policy supplier_message_bundle_items_tenant_read
on public.supplier_message_bundle_items
for select to authenticated
using (
  exists (
    select 1 from public.profiles p
    where p.id=(select auth.uid()) and p.client_id=supplier_message_bundle_items.client_id
  )
);
