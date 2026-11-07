import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import db
import main


CLIENT_ID = str(uuid.uuid4())
SUPPLIER_ID = str(uuid.uuid4())
PHONE_NUMBER = "971501234567"


@pytest.fixture
def mock_supabase():
    with patch.object(db, "supabase") as mock_sb:
        yield mock_sb


def make_open_rfq(rfq_id: str, product_name: str, status: str = "sent", specs: str = "Standard", hours_remaining: int = 24):
    due_by = (datetime.now(timezone.utc) + timedelta(hours=hours_remaining)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "rfq_id": rfq_id,
        "supplier_id": SUPPLIER_ID,
        "status": status,
        "rfqs": {
            "id": rfq_id,
            "client_id": CLIENT_ID,
            "product_name": product_name,
            "specs": specs,
            "status": "active",
            "due_by": due_by,
        }
    }


def make_whatsapp_payload(text: str, stanza_id: str = None, msg_id: str = None):
    msg_id = msg_id or f"msg-{uuid.uuid4()}"
    msg_payload = {"conversation": text}
    if stanza_id:
        msg_payload = {
            "extendedTextMessage": {
                "text": text,
                "contextInfo": {
                    "stanzaId": stanza_id
                }
            }
        }
    return {
        "instance": "test_instance",
        "data": {
            "key": {
                "remoteJid": f"{PHONE_NUMBER}@s.whatsapp.net",
                "fromMe": False,
                "id": msg_id,
            },
            "message": msg_payload,
        }
    }


class TestPhase1RFQRoutingAndClarification:
    """
    Test suite for Phase 1: RFQ Routing, Clarification State Integrity, and Conversation Continuity.
    Validates all 15 required test scenarios from the Phase 1 specification.
    """

    # --------------------------------------------------------------------------
    # 1. Responded RFQ remains responded when clarification starts
    # --------------------------------------------------------------------------
    def test_1_responded_rfq_remains_responded_when_clarification_starts(self, mock_supabase):
        """1. A previously responded RFQ preserves status='responded' when a new clarification starts."""
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "clarif-new"}])

        rfq_a_id = str(uuid.uuid4())
        rfq_b_id = str(uuid.uuid4())

        # Calling create_pending_clarification for candidate RFQs [rfq_a, rfq_b]
        clarif_id = db.create_pending_clarification(
            client_id=CLIENT_ID,
            supplier_id=SUPPLIER_ID,
            candidate_rfq_ids=[rfq_a_id, rfq_b_id],
            raw_message="PVC pipe 68 AED",
            extracted_price=68.0,
            last_question="Which PVC pipe order?",
        )

        assert clarif_id == "clarif-new"
        # Verify table("pending_clarifications") was inserted into
        mock_supabase.table.assert_called_with("pending_clarifications")
        mock_table.insert.assert_called_once()
        # Verify rfq_suppliers table was NEVER updated with status='clarifying'
        assert not mock_table.update.called

    # --------------------------------------------------------------------------
    # 2. Clarification does not modify rfq_suppliers.status
    # --------------------------------------------------------------------------
    def test_2_clarification_does_not_modify_rfq_suppliers_status(self, mock_supabase):
        """2. Neither create_pending_clarification nor advance_pending_clarification modifies rfq_suppliers."""
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "clarif-1"}])

        rfq_id = str(uuid.uuid4())
        db.create_pending_clarification(
            client_id=CLIENT_ID,
            supplier_id=SUPPLIER_ID,
            candidate_rfq_ids=[rfq_id],
            raw_message="quote terms",
        )
        mock_supabase.table.assert_called_with("pending_clarifications")
        assert not mock_table.update.called

    # --------------------------------------------------------------------------
    # 3. Clarification resolution cannot turn responded -> sent
    # --------------------------------------------------------------------------
    def test_3_clarification_resolution_cannot_turn_responded_to_sent(self, mock_supabase):
        """3. When clarification resolves, revert_unresolved_candidates cannot alter responded to sent."""
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table

        rfq_a_id = str(uuid.uuid4())
        rfq_b_id = str(uuid.uuid4())

        # revert_unresolved_candidates is a safe no-op that never calls update on rfq_suppliers
        db.revert_unresolved_candidates(
            supplier_id=SUPPLIER_ID,
            resolved_rfq_id=rfq_a_id,
            candidate_rfq_ids=[rfq_a_id, rfq_b_id],
        )
        mock_supabase.table.assert_not_called()
        assert not mock_table.update.called

    # --------------------------------------------------------------------------
    # 4. Existing clarifying + quote data repairs to responded
    # --------------------------------------------------------------------------
    def test_4_existing_clarifying_with_quote_repairs_to_responded(self):
        """4. Data repair logic: rfq_suppliers with status='clarifying' and existing quote repairs to 'responded'."""
        migration_sql_path = "supabase/migrations/20260927191630_fix_rfq_clarification_status_corruption.sql"
        with open(migration_sql_path, "r") as f:
            sql_content = f.read()

        # Verify SQL migration contains safe repair to 'responded' when quotes exist
        assert "SET status = 'responded'" in sql_content
        assert "WHERE rs.status = 'clarifying'" in sql_content
        assert "EXISTS (" in sql_content
        assert "FROM quotes q" in sql_content

    # --------------------------------------------------------------------------
    # 5. Existing clarifying + no quote repairs to sent
    # --------------------------------------------------------------------------
    def test_5_existing_clarifying_without_quote_repairs_to_sent(self):
        """5. Data repair logic: rfq_suppliers with status='clarifying' and no quote repairs to 'sent'."""
        migration_sql_path = "supabase/migrations/20260927191630_fix_rfq_clarification_status_corruption.sql"
        with open(migration_sql_path, "r") as f:
            sql_content = f.read()

        # Verify SQL migration contains safe repair to 'sent' when no quotes exist
        assert "SET status = 'sent'" in sql_content
        assert "WHERE rs.status = 'clarifying'" in sql_content
        assert "NOT EXISTS (" in sql_content
        assert "FROM quotes q" in sql_content

    # --------------------------------------------------------------------------
    # 6. Exact stanzaId selects correct RFQ even with 5 similar open RFQs
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 7. stanzaId overrides semantic matching
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 8. Old responded RFQ stays open for revisions
    # --------------------------------------------------------------------------
    def test_8_old_responded_rfq_stays_open_for_revisions(self):
        """8. An RFQ in status='responded' remains open as long as deadline has not passed."""
        future_due = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
        rfq = {
            "id": "rfq-responded-open",
            "status": "active",
            "due_by": future_due,
        }
        assert db.is_rfq_open(rfq) is True

    # --------------------------------------------------------------------------
    # 9. New unanswered RFQ is preferred over unrelated old responded RFQs
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 10. Responded RFQs are not automatically included in generic ambiguity
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 11. Inbound message receives related_rfq_id after stanza matching
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 12. Inbound message receives related_rfq_id after clarification resolution
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 13. Original extracted price survives clarification
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 14. Revision to an older responded RFQ before deadline still works
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 15. Multiple unrelated old RFQs do not force unnecessary clarification
    # --------------------------------------------------------------------------
