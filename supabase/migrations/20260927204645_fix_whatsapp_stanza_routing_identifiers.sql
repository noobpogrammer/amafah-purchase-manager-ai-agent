-- ============================================================
-- Fix WhatsApp Stanza Routing Identifiers & Outbound Message Identity
-- ============================================================

-- 1. Add sent_message_alt_id to rfq_suppliers
ALTER TABLE rfq_suppliers
    ADD COLUMN IF NOT EXISTS sent_message_alt_id text;

-- 2. Add external_message_id and external_alt_message_id to message_log
ALTER TABLE message_log
    ADD COLUMN IF NOT EXISTS external_message_id text,
    ADD COLUMN IF NOT EXISTS external_alt_message_id text;

-- 3. Backfill external_message_id from evolution_message_id for existing rows
UPDATE message_log
SET external_message_id = evolution_message_id
WHERE external_message_id IS NULL AND evolution_message_id IS NOT NULL;

-- 4. Create indexes for fast, fail-closed quoted stanza lookups
CREATE INDEX IF NOT EXISTS idx_rfq_suppliers_sent_message_id ON rfq_suppliers(sent_message_id);
CREATE INDEX IF NOT EXISTS idx_rfq_suppliers_sent_message_alt_id ON rfq_suppliers(sent_message_alt_id);
CREATE INDEX IF NOT EXISTS idx_message_log_external_message_id ON message_log(external_message_id);
CREATE INDEX IF NOT EXISTS idx_message_log_external_alt_message_id ON message_log(external_alt_message_id);
CREATE INDEX IF NOT EXISTS idx_message_log_evolution_message_id ON message_log(evolution_message_id);
