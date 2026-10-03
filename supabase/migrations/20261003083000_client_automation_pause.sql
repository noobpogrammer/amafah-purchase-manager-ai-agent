alter table public.clients
  add column if not exists automation_paused boolean not null default false,
  add column if not exists automation_paused_at timestamptz,
  add column if not exists automation_pause_reason text;

comment on column public.clients.automation_paused is
  'When true, supplier inbound messages are logged and escalated for human handling only; automated RFQ processing/reminders/finalization are paused.';

comment on column public.clients.automation_paused_at is
  'Timestamp when client automation was paused.';

comment on column public.clients.automation_pause_reason is
  'Operator-visible reason for the pause.';
