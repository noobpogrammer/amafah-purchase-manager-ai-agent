-- Phase 11: Variant-Aware Ranking Schema
-- Adds best_quote_id foreign key referencing quotes(id) to rfq_rankings

ALTER TABLE rfq_rankings
ADD COLUMN IF NOT EXISTS best_quote_id UUID
REFERENCES quotes(id)
ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_rfq_rankings_best_quote
ON rfq_rankings(best_quote_id);
