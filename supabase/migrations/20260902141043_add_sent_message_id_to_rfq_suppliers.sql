-- Add sent_message_id column to rfq_suppliers to store Evolution API / WhatsApp message IDs
ALTER TABLE rfq_suppliers ADD COLUMN IF NOT EXISTS sent_message_id text;
