-- ============================================================
-- Migration: Add round_number column to pending_clarifications
-- ============================================================

alter table pending_clarifications
add column if not exists round_number integer not null default 1;
