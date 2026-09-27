import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import db
import main
from policy_validator import ActionProposal, validate_action


CLIENT_ID = str(uuid.uuid4())
SUPPLIER_ID = str(uuid.uuid4())
PHONE_NUMBER = "971501234567"


@pytest.fixture
def mock_supabase():
    with patch.object(db, "supabase") as mock_sb:
        yield mock_sb


@pytest.fixture(autouse=True)
def mock_groq_and_decisions():
    with patch("main.groq_client.reason_about_procurement_message", return_value={"tool_name": "record_quote", "arguments": {"price": 45.0}}), \
         patch("db.record_agent_decision", return_value={"id": "mock-dec-123"}):
        yield


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
    @pytest.mark.asyncio
    async def test_1_outbound_evolution_send_response_id_stored_correctly(self):
        """1. Evolution API send response with key.id and alt id stores canonical & alt IDs in DB."""
        rfq_id = str(uuid.uuid4())
        msg_log_id = str(uuid.uuid4())
        
        mock_response = {
            "key": {
                "remoteJid": f"{PHONE_NUMBER}@s.whatsapp.net",
                "fromMe": True,
                "id": "3EB0428E3498ABC1",
            },
            "messageId": "ALT_EVO_MSG_ID_999",
            "status": "PENDING",
        }

        canonical, alt = main.extract_evolution_message_ids(mock_response)
        assert canonical == "3EB0428E3498ABC1"
        assert alt == "ALT_EVO_MSG_ID_999"

        with patch("main.send_whatsapp_message", return_value=mock_response), \
             patch("db.mark_message_sending", return_value=True), \
             patch("db.mark_message_sent") as mock_mark_sent, \
             patch("db.update_rfq_supplier_sent_message_id") as mock_update_rfq_sent, \
             patch("asyncio.sleep", new_callable=AsyncMock):

            main.outbound_queue = asyncio.Queue()
            await main.enqueue_message(
                phone_number=PHONE_NUMBER,
                message="RFQ message text",
                rfq_id=rfq_id,
                supplier_id=SUPPLIER_ID,
                message_log_id=msg_log_id,
            )

            worker_task = asyncio.create_task(main.outbound_worker())
            await main.outbound_queue.join()
            worker_task.cancel()

            mock_mark_sent.assert_called_once_with(
                msg_log_id,
                evolution_message_id="3EB0428E3498ABC1",
                external_message_id="3EB0428E3498ABC1",
                external_alt_message_id="ALT_EVO_MSG_ID_999",
            )
            mock_update_rfq_sent.assert_called_once_with(
                rfq_id,
                SUPPLIER_ID,
                "3EB0428E3498ABC1",
                sent_message_alt_id="ALT_EVO_MSG_ID_999",
            )

    # --------------------------------------------------------------------------
    # 2. Inbound contextInfo.stanzaId matches stored outbound ID
    # --------------------------------------------------------------------------
    def test_2_inbound_context_info_stanza_id_matches_stored_outbound_id(self):
        """2. Inbound WhatsApp message with contextInfo.stanzaId resolves exactly to stored outbound RFQ."""
        rfq_a_id = str(uuid.uuid4())
        stanza_id = "3EB0428E3498ABC1"
        rfq_a_entry = make_open_rfq(rfq_a_id, "PVC Pipe 1 Inch", status="sent", sent_message_id=stanza_id)

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[rfq_a_entry]), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=rfq_a_entry) as mock_lookup, \
             patch("db.log_message", return_value="inbound-log-1"), \
             patch("db.update_message_related_rfq") as mock_update_rel, \
             patch("main.execute_validated_action", new_callable=AsyncMock, return_value={"status": "recorded_via_quoted_message"}):

            payload = make_whatsapp_payload("45 AED per piece, delivery 2 days", stanza_id=stanza_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert response.json()["status"] == "recorded_via_quoted_message"
            mock_lookup.assert_called_with(SUPPLIER_ID, stanza_id)
            mock_update_rel.assert_called_with("inbound-log-1", rfq_a_id)

    # --------------------------------------------------------------------------
    # 3. Quoted reply routes to the exact old responded RFQ
    # --------------------------------------------------------------------------
    def test_3_quoted_reply_routes_to_exact_old_responded_rfq(self):
        """3. Stanza reply referencing an old responded RFQ routes exclusively to that RFQ."""
        old_rfq_id = str(uuid.uuid4())
        new_rfq_id = str(uuid.uuid4())
        old_stanza_id = "STANZA_OLD_RESP_123"

        old_rfq_entry = make_open_rfq(old_rfq_id, "PVC Pipe 1 Inch", status="responded", sent_message_id=old_stanza_id)
        new_rfq_entry = make_open_rfq(new_rfq_id, "PVC Pipe 1 Inch", status="sent", sent_message_id="STANZA_NEW_456")

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[old_rfq_entry, new_rfq_entry]), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=old_rfq_entry), \
             patch("db.log_message", return_value="inbound-log-2"), \
             patch("db.update_message_related_rfq") as mock_update_rel, \
             patch("main.execute_validated_action", new_callable=AsyncMock, return_value={"status": "recorded_via_quoted_message"}):

            payload = make_whatsapp_payload("Actually revised price 43 AED", stanza_id=old_stanza_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert response.json()["status"] == "recorded_via_quoted_message"
            mock_update_rel.assert_called_with("inbound-log-2", old_rfq_id)

    # --------------------------------------------------------------------------
    # 4. Exact stanza reply overrides a newer unanswered RFQ
    # --------------------------------------------------------------------------
    def test_4_exact_stanza_reply_overrides_newer_unanswered_rfq(self):
        """4. Stanza match on older responded RFQ overrides the Tier-4 rule that would prefer unanswered RFQ."""
        old_rfq_id = str(uuid.uuid4())
        new_unanswered_id = str(uuid.uuid4())
        target_stanza_id = "STANZA_TARGET_OLD"

        old_rfq = make_open_rfq(old_rfq_id, "PVC Pipe 1 Inch", status="responded", sent_message_id=target_stanza_id)
        new_unanswered_rfq = make_open_rfq(new_unanswered_id, "PVC Pipe 1 Inch", status="sent", sent_message_id="STANZA_NEW_UNANSWERED")

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER}

        captured_ctx = None

        async def fake_execute(validation, context, *args, **kwargs):
            nonlocal captured_ctx
            captured_ctx = context
            return {"status": "recorded_via_quoted_message"}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[old_rfq, new_unanswered_rfq]), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=old_rfq), \
             patch("db.log_message", return_value="inbound-log-3"), \
             patch("db.update_message_related_rfq"), \
             patch("main.execute_validated_action", side_effect=fake_execute):

            payload = make_whatsapp_payload("43 AED revised", stanza_id=target_stanza_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert captured_ctx is not None
            assert captured_ctx.matched_rfq_id == old_rfq_id
            assert captured_ctx.match_source == "exact_stanza"

    # --------------------------------------------------------------------------
    # 5. Exact stanza reply overrides active conversation context
    # --------------------------------------------------------------------------
    def test_5_exact_stanza_reply_overrides_active_conversation_context(self):
        """5. Exact stanza match on RFQ-A overrides recent conversation context that points to RFQ-B."""
        rfq_a_id = str(uuid.uuid4())
        rfq_b_id = str(uuid.uuid4())
        stanza_a_id = "STANZA_RFQ_A_ORIG"

        rfq_a = make_open_rfq(rfq_a_id, "PVC Pipe 1 Inch", status="responded", sent_message_id=stanza_a_id)
        rfq_b = make_open_rfq(rfq_b_id, "PVC Pipe 2 Inch", status="sent", sent_message_id="STANZA_RFQ_B")

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER}

        conv_history = [
            {"direction": "outbound", "related_rfq_id": rfq_b_id, "content": "Question about 2 inch pipe"},
            {"direction": "inbound", "related_rfq_id": rfq_b_id, "content": "Looking into it"},
        ]

        captured_ctx = None

        async def fake_execute(validation, context, *args, **kwargs):
            nonlocal captured_ctx
            captured_ctx = context
            return {"status": "recorded_via_quoted_message"}


        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[rfq_a, rfq_b]), \
             patch("db.get_supplier_conversation_history", return_value=conv_history), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=rfq_a), \
             patch("db.log_message", return_value="inbound-log-5"), \
             patch("db.update_message_related_rfq"), \
             patch("main.execute_validated_action", side_effect=fake_execute):

            payload = make_whatsapp_payload("41 AED for the 1 inch", stanza_id=stanza_a_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert captured_ctx is not None
            assert captured_ctx.matched_rfq_id == rfq_a_id
            assert captured_ctx.match_source == "exact_stanza"

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
    def test_7_unknown_quoted_stanza_still_fails_closed(self):
        """7. Inbound message quoting an unknown stanzaId returns ignored and fails closed."""
        unknown_stanza_id = "STANZA_UNKNOWN_999"
        open_rfqs = [make_open_rfq(str(uuid.uuid4()), "Copper Wire", status="sent")]

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Wire Supplier", "phone_number": PHONE_NUMBER}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message") as mock_complete_claim, \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.log_message", return_value="inbound-log-7"), \
             patch("main.execute_validated_action") as mock_exec:

            payload = make_whatsapp_payload("Here is our quote 50 AED", stanza_id=unknown_stanza_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert response.json()["status"] == "ignored"
            assert "quoted_stanza_id already responded or closed" in response.json()["reason"]
            mock_exec.assert_not_called()
            mock_complete_claim.assert_called_once()

    # --------------------------------------------------------------------------
    # 8. Unknown quoted stanza does not get semantically attached to another RFQ
    # --------------------------------------------------------------------------
    def test_8_unknown_quoted_stanza_does_not_get_semantically_attached_to_another_rfq(self):
        """8. Unmatched quoted stanza must NOT fall back to semantic/Groq routing on other RFQs."""
        rfq_other_id = str(uuid.uuid4())
        rfq_other = make_open_rfq(rfq_other_id, "Copper Wire 2.5mm", status="sent")

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Wire Supplier", "phone_number": PHONE_NUMBER}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.log_message", return_value="inbound-log-8"), \
             patch("groq_client.reason_about_procurement_message") as mock_groq, \
             patch("main.execute_validated_action") as mock_exec:

            # Even though the text mentions Copper Wire, the unknown quoted stanza blocks fallback
            payload = make_whatsapp_payload("Copper Wire 2.5mm is 50 AED", stanza_id="UNKNOWN_STANZA_XYZ")
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert response.json()["status"] == "ignored"
            mock_groq.assert_not_called()
            mock_exec.assert_not_called()

    # --------------------------------------------------------------------------
    # 9. Successful stanza match updates inbound message_log.related_rfq_id
    # --------------------------------------------------------------------------
    def test_9_successful_stanza_match_updates_inbound_message_log_related_rfq_id(self):
        """9. When quoted stanza matches, the inbound message_log row is updated with related_rfq_id."""
        rfq_id = str(uuid.uuid4())
        stanza_id = "STANZA_MATCH_REL_LOG"
        rfq_entry = make_open_rfq(rfq_id, "PVC Pipe 1 Inch", status="sent", sent_message_id=stanza_id)

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[rfq_entry]), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=rfq_entry), \
             patch("db.log_message", return_value="inbound-msg-uuid-999") as mock_log, \
             patch("db.update_message_related_rfq") as mock_update_rel, \
             patch("main.execute_validated_action", new_callable=AsyncMock, return_value={"status": "recorded_via_quoted_message"}):

            payload = make_whatsapp_payload("68 AED delivery tomorrow", stanza_id=stanza_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            mock_log.assert_called_once_with(CLIENT_ID, SUPPLIER_ID, "inbound", "68 AED delivery tomorrow")
            mock_update_rel.assert_called_once_with("inbound-msg-uuid-999", rfq_id)

    # --------------------------------------------------------------------------
    # 10. Successful stanza match allows quote revision on responded-but-open RFQ
    # --------------------------------------------------------------------------
    def test_10_successful_stanza_match_allows_quote_revision_on_responded_but_open_rfq(self):
        """10. Quoted reply on a previously responded RFQ before deadline records quote revision."""
        rfq_id = str(uuid.uuid4())
        stanza_id = "STANZA_REVISION_FLOW"
        # RFQ is in responded status, deadline has not passed (open)
        responded_open_rfq = make_open_rfq(
            rfq_id,
            "PVC Pipe 1 Inch",
            status="responded",
            hours_remaining=12,
            sent_message_id=stanza_id,
        )

        client_mock = {"id": CLIENT_ID, "name": "Test Client"}
        supplier_mock = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER}

        with patch("db.claim_webhook_message", return_value=True), \
             patch("db.complete_webhook_message"), \
             patch("db.get_client_by_instance", return_value=client_mock), \
             patch("db.get_supplier_by_phone", return_value=supplier_mock), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[responded_open_rfq]), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=responded_open_rfq), \
             patch("db.log_message", return_value="inbound-log-10"), \
             patch("db.update_message_related_rfq") as mock_update_rel, \
             patch("main.execute_validated_action", new_callable=AsyncMock) as mock_exec:

            mock_exec.return_value = {"status": "recorded_via_quoted_message", "price": 42.0}

            payload = make_whatsapp_payload("Revision: We can do 42 AED now", stanza_id=stanza_id)
            client = TestClient(main.app)
            response = client.post("/webhook/whatsapp", json=payload)

            assert response.status_code == 200
            assert response.json()["status"] == "recorded_via_quoted_message"
            assert response.json()["price"] == 42.0
            mock_update_rel.assert_called_with("inbound-log-10", rfq_id)
            mock_exec.assert_called_once()
