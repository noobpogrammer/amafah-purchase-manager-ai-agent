-- ============================================================
-- Migration: Convert suppliers.category to text[] array
-- ============================================================

do $$
begin
    if exists (
        select 1 from information_schema.columns 
        where table_name = 'suppliers' and column_name = 'category' and data_type != 'ARRAY'
    ) then
        alter table suppliers 
        alter column category type text[] using case when category is null then null else array[category] end;
    end if;
end $$;
