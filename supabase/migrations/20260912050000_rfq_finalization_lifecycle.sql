-- ============================================================
-- Phase 7.3: RFQ Finalization Lifecycle, Idempotent Ranking & Notifications
-- ============================================================

-- 1. Add finalization_status and finalized_at to rfqs table
ALTER TABLE rfqs 
ADD COLUMN IF NOT EXISTS finalization_status TEXT DEFAULT 'pending'
    CHECK (finalization_status IN ('pending', 'processing', 'completed')),
ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMPTZ;

-- Backfill existing records:
-- Existing closed/cancelled RFQs are marked 'completed' so they are not treated as unfinished jobs
UPDATE rfqs 
SET finalization_status = 'completed',
    finalized_at = COALESCE(finalized_at, now())
WHERE status IN ('closed', 'cancelled') 
  AND (finalization_status IS NULL OR finalization_status = 'pending');

-- Existing active RFQs are marked 'pending'
UPDATE rfqs 
SET finalization_status = 'pending'
WHERE status = 'active' 
  AND finalization_status IS NULL;

-- Index for efficient scanning of active and processing RFQs
CREATE INDEX IF NOT EXISTS idx_rfqs_finalization_status ON rfqs(finalization_status);


-- 2. Deduplicate existing rfq_rankings and add UNIQUE(rfq_id) constraint
-- Retain the latest ranking per rfq_id by created_at and tie-breaker id
DELETE FROM rfq_rankings a
USING rfq_rankings b
WHERE a.rfq_id = b.rfq_id
  AND (
      a.created_at < b.created_at 
      OR (a.created_at = b.created_at AND a.id < b.id)
  );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_rfq_rankings_rfq_id'
    ) THEN
        ALTER TABLE rfq_rankings ADD CONSTRAINT uq_rfq_rankings_rfq_id UNIQUE (rfq_id);
    END IF;
END $$;


-- 3. Add event_key to message_log for business-event idempotency
ALTER TABLE message_log
ADD COLUMN IF NOT EXISTS event_key TEXT;

-- Create partial unique index on event_key (only applies to rows where event_key is not null)
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_log_event_key 
ON message_log(event_key) 
WHERE event_key IS NOT NULL;


-- 4. Atomic claim_rfq_for_finalization RPC
CREATE OR REPLACE FUNCTION claim_rfq_for_finalization(p_rfq_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_claimed BOOLEAN := FALSE;
BEGIN
    -- Atomically transition rfqs from 'active' -> 'closed' with finalization_status = 'processing'
    UPDATE rfqs
    SET status = 'closed',
        finalization_status = 'processing'
    WHERE id = p_rfq_id
      AND status = 'active'
      AND (finalization_status IS NULL OR finalization_status = 'pending')
      AND (
          (due_by IS NOT NULL AND due_by <= now())
          OR (due_by IS NULL AND created_at + (COALESCE(deadline_hours, 24) || ' hours')::interval <= now())
      );

    IF FOUND THEN
        v_claimed := TRUE;

        -- Cascade child cleanup: mark pending suppliers no_response
        UPDATE rfq_suppliers
        SET status = 'no_response'
        WHERE rfq_id = p_rfq_id
          AND status IN ('sent', 'clarifying');

        -- Abandon any unresolved pending clarifications for this RFQ
        UPDATE pending_clarifications
        SET status = 'abandoned'
        WHERE status = 'awaiting_reply'
          AND p_rfq_id = ANY(pending_rfq_ids);
    END IF;

    RETURN v_claimed;
END;
$$;
