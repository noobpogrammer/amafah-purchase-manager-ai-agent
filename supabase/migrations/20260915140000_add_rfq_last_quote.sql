-- ============================================================
-- Phase 15: Last Quote–Aware Negotiation Policy
-- Add last_quote historical reference column to rfqs table
-- ============================================================

ALTER TABLE rfqs 
  ADD COLUMN IF NOT EXISTS last_quote numeric(12, 2);
