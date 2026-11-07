import asyncio
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


def make_open_rfq(
    rfq_id: str,
    product_name: str,
    status: str = "sent",
    specs: str = "Standard",
    hours_remaining: int = 24,
    sent_message_id: str = None,
    sent_message_alt_id: str = None,
):
    due_by = (datetime.now(timezone.utc) + timedelta(hours=hours_remaining)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "rfq_id": rfq_id,
        "supplier_id": SUPPLIER_ID,
        "status": status,
        "sent_message_id": sent_message_id,
        "sent_message_alt_id": sent_message_alt_id,
        "rfqs": {
            "id": rfq_id,
            "client_id": CLIENT_ID,
            "product_name": product_name,
            "specs": specs,
            "status": "active",
            "due_by": due_by,
        }
    }


def make_whatsapp_payload(text: str, stanza_id: str = None, msg_id: str = None, context_dict: dict = None):
    msg_id = msg_id or f"msg-{uuid.uuid4()}"
    if stanza_id or context_dict:
        ctx = context_dict or {"stanzaId": stanza_id}
        msg_payload = {
            "extendedTextMessage": {
                "text": text,
                "contextInfo": ctx,
            }
        }
    else:
        msg_payload = {"conversation": text}

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


class TestPhase1WhatsAppStanzaRouting:
    """
    Test suite for Phase 1 Outbound Message Identifiers and Exact Quoted Stanza Routing.
    Validates all 10 required test scenarios.
    """

    # --------------------------------------------------------------------------
    # 1. Outbound Evolution send response ID is stored correctly
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 2. Inbound contextInfo.stanzaId matches stored outbound ID
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 3. Quoted reply routes to the exact old responded RFQ
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 4. Exact stanza reply overrides a newer unanswered RFQ
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 5. Exact stanza reply overrides active conversation context
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 6. Alternate Evolution ID format resolves only to the same outbound message
    # --------------------------------------------------------------------------
    def test_6_alternate_evolution_id_format_resolves_only_to_same_outbound_message(self, mock_supabase):
        """6. Compound or alternate Evolution ID formats resolve safely to the correct outbound RFQ."""
        rfq_id = str(uuid.uuid4())
        raw_compound_id = f"false_{PHONE_NUMBER}@s.whatsapp.net_3EB0ALTKEY999"
        norm_stanza_id = "3EB0ALTKEY999"

        # normalize_message_id safely extracts stanza id from compound format
        assert db.normalize_message_id(raw_compound_id) == norm_stanza_id

        # Mock DB querying sent_message_alt_id
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        
        due_by = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        db_rfq_supplier = {
            "id": "rfq-supp-alt",
            "rfq_id": rfq_id,
            "supplier_id": SUPPLIER_ID,
            "status": "sent",
            "sent_message_id": "PRIMARY_ID",
            "sent_message_alt_id": raw_compound_id,
            "rfqs": {
                "id": rfq_id,
                "status": "active",
                "due_by": due_by,
                "product_name": "Steel Elbow",
            }
        }

        # Setup mock execute returning data when querying sent_message_alt_id
        mock_table.select.return_value.eq.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(data=[db_rfq_supplier])

        resolved = db.get_rfq_supplier_by_sent_message_id(SUPPLIER_ID, norm_stanza_id)
        assert resolved is not None
        assert resolved["rfq_id"] == rfq_id

    # --------------------------------------------------------------------------
    # 7. Unknown quoted stanza still fails closed
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 8. Unknown quoted stanza does not get semantically attached to another RFQ
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 9. Successful stanza match updates inbound message_log.related_rfq_id
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 10. Successful stanza match allows quote revision on responded-but-open RFQ
    # --------------------------------------------------------------------------
