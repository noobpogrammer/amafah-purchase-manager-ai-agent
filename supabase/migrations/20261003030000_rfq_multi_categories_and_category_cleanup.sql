-- RFQs may target multiple supplier categories while preserving the legacy scalar category.
alter table if exists public.rfqs
  add column if not exists categories text[];

update public.rfqs
set categories = case
  when category is not null and btrim(category) <> '' then array[category]
  else '{}'::text[]
end
where categories is null;

alter table public.rfqs
  alter column categories set default '{}'::text[];

create index if not exists idx_rfqs_categories_gin
  on public.rfqs using gin (categories);

-- Helper removes a category from live supplier/RFQ category arrays for one tenant.
create or replace function public.delete_category_clean(
  p_client_id uuid,
  p_category text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_name text;
  v_deleted int := 0;
  v_suppliers int := 0;
  v_rfqs int := 0;
begin
  v_name := btrim(coalesce(p_category, ''));
  if v_name = '' then
    raise exception 'Category name is required';
  end if;

  delete from categories
  where client_id = p_client_id and name = v_name;
  get diagnostics v_deleted = row_count;

  update suppliers
  set category = coalesce((
    select array_agg(x order by ord)
    from unnest(coalesce(suppliers.category, '{}'::text[])) with ordinality t(x, ord)
    where x <> v_name
  ), '{}'::text[])
  where client_id = p_client_id
    and v_name = any(coalesce(category, '{}'::text[]));
  get diagnostics v_suppliers = row_count;

  update rfqs
  set
    categories = coalesce((
      select array_agg(x order by ord)
      from unnest(coalesce(rfqs.categories, case when rfqs.category is null then '{}'::text[] else array[rfqs.category] end)) with ordinality t(x, ord)
      where x <> v_name
    ), '{}'::text[]),
    category = case
      when category = v_name then (
        select x
        from unnest(coalesce(rfqs.categories, '{}'::text[])) with ordinality t(x, ord)
        where x <> v_name
        order by ord
        limit 1
      )
      else category
    end
  where client_id = p_client_id
    and (
      category = v_name
      or v_name = any(coalesce(categories, '{}'::text[]))
    );
  get diagnostics v_rfqs = row_count;

  return jsonb_build_object(
    'deleted', v_deleted > 0,
    'category', v_name,
    'suppliers_updated', v_suppliers,
    'rfqs_updated', v_rfqs
  );
end;
$$;
