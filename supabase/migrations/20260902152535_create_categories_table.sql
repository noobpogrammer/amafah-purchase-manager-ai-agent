-- Create categories table for custom category management per client
create table if not exists categories (
    id          uuid primary key default gen_random_uuid(),
    client_id   uuid not null references clients(id) on delete cascade,
    name        text not null,
    created_at  timestamptz not null default now(),
    unique(client_id, name)
);

-- Seed default categories for demo client
insert into categories (client_id, name)
select 'd88c52ad-3d0b-42e9-86f1-b9f70018856b'::uuid, cat
from unnest(array['Electronics', 'Hardware', 'Plumbing', 'Electrical', 'Tools', 'Building Materials', 'General']) as cat
on conflict (client_id, name) do nothing;
