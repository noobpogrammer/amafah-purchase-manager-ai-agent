-- ============================================================
-- Phase 6: Controlled Autonomous Negotiation Loop
-- Add acceptable price range to rfqs and negotiation attempts to rfq_suppliers
-- ============================================================

ALTER TABLE rfqs 
  ADD COLUMN IF NOT EXISTS acceptable_price_min numeric(12, 2),
  ADD COLUMN IF NOT EXISTS acceptable_price_max numeric(12, 2);

ALTER TABLE rfq_suppliers 
  ADD COLUMN IF NOT EXISTS negotiation_attempts integer NOT NULL DEFAULT 0;
