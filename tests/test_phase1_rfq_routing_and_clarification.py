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
    @pytest.mark.asyncio
    async def test_6_exact_stanza_id_selects_correct_rfq_with_5_similar_open_rfqs(self):
        """6. Exact stanzaId match locks to the targeted RFQ even when 5 similar open RFQs exist."""
        client = TestClient(main.app)
        target_rfq_id = str(uuid.uuid4())
        stanza_id = "STANZA-EXACT-12345"

        five_similar_rfqs = [
            make_open_rfq(str(uuid.uuid4()), "PVC Pipe 1 Inch", status="responded"),
            make_open_rfq(str(uuid.uuid4()), "PVC Pipe 1 Inch", status="responded"),
            make_open_rfq(target_rfq_id, "PVC Pipe 1 Inch", status="sent"),
            make_open_rfq(str(uuid.uuid4()), "PVC Pipe 1 Inch", status="sent"),
            make_open_rfq(str(uuid.uuid4()), "PVC Pipe 1 Inch", status="responded"),
        ]
        target_rfq_entry = five_similar_rfqs[2]

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=target_rfq_entry) as mock_stanza_lookup, \
             patch("db.get_open_rfqs_for_supplier", return_value=five_similar_rfqs), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-log-1"), \
             patch("db.update_message_related_rfq") as mock_update_msg_rfq, \
             patch("db.record_quote") as mock_record_quote, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": target_rfq_id, "price": 68.0, "delivery_time": "2 days"}
             }) as mock_groq:

            payload = make_whatsapp_payload("68 AED per piece, delivery 2 days", stanza_id=stanza_id)
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded_via_quoted_message"
            assert resp.json()["rfq_id"] == target_rfq_id
            mock_stanza_lookup.assert_called_once_with(SUPPLIER_ID, stanza_id)
            mock_record_quote.assert_called_once_with(
                rfq_id=target_rfq_id,
                supplier_id=SUPPLIER_ID,
                price=68.0,
                delivery_time="2 days",
                quality_notes=None,
                raw_message="68 AED per piece, delivery 2 days",
            )
            # Verify inbound message logged with related_rfq_id
            mock_update_msg_rfq.assert_any_call("inbound-log-1", target_rfq_id)

    # --------------------------------------------------------------------------
    # 7. stanzaId overrides semantic matching
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_7_stanza_id_overrides_semantic_matching(self):
        """7. A direct stanzaId reply to RFQ-A takes absolute priority even if text mentions RFQ-B's product."""
        client = TestClient(main.app)
        rfq_a_id = str(uuid.uuid4())
        rfq_b_id = str(uuid.uuid4())

        rfq_a_entry = make_open_rfq(rfq_a_id, "PVC Pipe 1 Inch", status="responded")
        rfq_b_entry = make_open_rfq(rfq_b_id, "Brass Valve 2 Inch", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=rfq_a_entry), \
             patch("db.get_open_rfqs_for_supplier", return_value=[rfq_a_entry, rfq_b_entry]), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=[{"rfq_id": rfq_a_id, "price": 45.0}]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-log-7"), \
             patch("db.update_message_related_rfq"), \
             patch("db.record_quote") as mock_record_quote, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": rfq_a_id, "price": 43.0}
             }) as mock_groq:

            # Supplier message mentions "Brass Valve" text but explicitly replied with stanza to RFQ-A
            payload = make_whatsapp_payload("Actually for Brass Valve we can do 43 AED", stanza_id="STANZA-RFQ-A")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded_via_quoted_message"
            assert resp.json()["rfq_id"] == rfq_a_id
            mock_record_quote.assert_called_once()
            assert mock_record_quote.call_args.kwargs["rfq_id"] == rfq_a_id
            assert mock_record_quote.call_args.kwargs["price"] == 43.0

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
    @pytest.mark.asyncio
    async def test_9_new_unanswered_rfq_preferred_over_unrelated_old_responded_rfqs(self):
        """
        9. Given:
        RFQ-A: PVC Pipe (responded, quote=45)
        RFQ-B: PVC Pipe (responded, quote=68)
        RFQ-C: PVC Pipe (sent, no quote)
        Supplier sends generic: "70 AED, delivery 2 days"
        Expected: RFQ-C is preferred and quote recorded for RFQ-C without asking clarification.
        """
        client = TestClient(main.app)
        rfq_a_id = "rfq-A-45"
        rfq_b_id = "rfq-B-68"
        rfq_c_id = "rfq-C-unanswered"

        rfq_a = make_open_rfq(rfq_a_id, "PVC Pipe 1 Inch", status="responded")
        rfq_b = make_open_rfq(rfq_b_id, "PVC Pipe 1 Inch", status="responded")
        rfq_c = make_open_rfq(rfq_c_id, "PVC Pipe 1 Inch", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}
        prior_quotes = [
            {"rfq_id": rfq_a_id, "price": 45.0},
            {"rfq_id": rfq_b_id, "price": 68.0},
        ]

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[rfq_a, rfq_b, rfq_c]), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=prior_quotes), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-log-9"), \
             patch("db.update_message_related_rfq") as mock_update_rfq, \
             patch("db.record_quote") as mock_record_quote, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": rfq_c_id, "price": 70.0, "delivery_time": "2 days"}
             }) as mock_reasoner:

            payload = make_whatsapp_payload("70 AED, delivery 2 days")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded"
            assert resp.json()["rfq_id"] == rfq_c_id

            # Verify reasoner received only RFQ-C as active candidate (unrelated responded RFQs excluded)
            called_ctx = mock_reasoner.call_args[0][1]
            candidate_ids = [e.get("rfqs", e).get("id") for e in called_ctx.open_rfqs]
            assert candidate_ids == [rfq_c_id]

            mock_record_quote.assert_called_once_with(
                rfq_id=rfq_c_id,
                supplier_id=SUPPLIER_ID,
                price=70.0,
                delivery_time="2 days",
                quality_notes=None,
                raw_message="70 AED, delivery 2 days",
            )
            mock_update_rfq.assert_any_call("inbound-log-9", rfq_c_id)

    # --------------------------------------------------------------------------
    # 10. Responded RFQs are not automatically included in generic ambiguity
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_10_responded_rfqs_not_included_in_generic_ambiguity(self):
        """10. When 2 unanswered RFQs exist alongside 3 responded RFQs, candidate set is strictly the 2 unanswered RFQs."""
        client = TestClient(main.app)
        rfq_u1_id = "rfq-unanswered-1"
        rfq_u2_id = "rfq-unanswered-2"
        rfq_r1_id = "rfq-responded-1"
        rfq_r2_id = "rfq-responded-2"
        rfq_r3_id = "rfq-responded-3"

        all_rfqs = [
            make_open_rfq(rfq_r1_id, "Cement 50kg", status="responded"),
            make_open_rfq(rfq_r2_id, "Steel Bar 12mm", status="responded"),
            make_open_rfq(rfq_r3_id, "PVC Pipe 1 Inch", status="responded"),
            make_open_rfq(rfq_u1_id, "Copper Wire 2.5mm", status="sent"),
            make_open_rfq(rfq_u2_id, "Copper Wire 4.0mm", status="sent"),
        ]
        prior_quotes = [
            {"rfq_id": rfq_r1_id, "price": 20.0},
            {"rfq_id": rfq_r2_id, "price": 40.0},
            {"rfq_id": rfq_r3_id, "price": 15.0},
        ]

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "General Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=all_rfqs), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=prior_quotes), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-log-10"), \
             patch("db.update_message_related_rfq"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "request_clarification",
                 "arguments": {
                     "candidate_rfq_ids": [rfq_u1_id, rfq_u2_id],
                     "clarifying_question": "Which wire thickness are you quoting: 2.5mm or 4.0mm?",
                 }
             }) as mock_groq, \
             patch("db.create_pending_clarification", return_value="clarif-10"):

            payload = make_whatsapp_payload("50 AED per roll")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "clarification_needed"
            # Verify reasoner only received the 2 unanswered RFQs
            called_ctx = mock_groq.call_args[0][1]
            candidate_ids = [e.get("rfqs", e).get("id") for e in called_ctx.open_rfqs]
            assert set(candidate_ids) == {rfq_u1_id, rfq_u2_id}

    # --------------------------------------------------------------------------
    # 11. Inbound message receives related_rfq_id after stanza matching
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_11_inbound_message_receives_related_rfq_id_after_stanza_matching(self):
        """11. Inbound message log row is updated with related_rfq_id upon stanza match."""
        client = TestClient(main.app)
        rfq_id = str(uuid.uuid4())
        rfq_entry = make_open_rfq(rfq_id, "LED Lights", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=rfq_entry), \
             patch("db.get_open_rfqs_for_supplier", return_value=[rfq_entry]), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-msg-11"), \
             patch("db.update_message_related_rfq") as mock_update_rfq, \
             patch("db.record_quote"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": rfq_id, "price": 12.5}
             }):

            payload = make_whatsapp_payload("12.5 AED each", stanza_id="STANZA-LED-11")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            # Assert related_rfq_id was updated for the inbound message
            mock_update_rfq.assert_called_with("inbound-msg-11", rfq_id)

    # --------------------------------------------------------------------------
    # 12. Inbound message receives related_rfq_id after clarification resolution
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_12_inbound_message_receives_related_rfq_id_after_clarification_resolution(self):
        """12. Inbound message log row receives related_rfq_id when a pending clarification is resolved."""
        client = TestClient(main.app)
        rfq_resolved_id = "rfq-2hr-order"
        rfq_other_id = "rfq-24hr-order"

        cand_rfqs = [
            make_open_rfq(rfq_resolved_id, "PVC Pipe 1 Inch", specs="2-hour urgent delivery"),
            make_open_rfq(rfq_other_id, "PVC Pipe 1 Inch", specs="24-hour standard delivery"),
        ]
        pending = {
            "id": "clarif-session-12",
            "client_id": CLIENT_ID,
            "supplier_id": SUPPLIER_ID,
            "pending_rfq_ids": [rfq_resolved_id, rfq_other_id],
            "raw_message": "68 AED per piece, delivery in 2 days",
            "extracted_price": 68.0,
            "extracted_delivery": "2 days",
            "round_number": 1,
            "status": "awaiting_reply",
        }

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_pending_clarification_for_supplier", return_value=pending), \
             patch("db.get_rfqs_by_ids", return_value=cand_rfqs), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-msg-12"), \
             patch("db.update_message_related_rfq") as mock_update_rfq, \
             patch("db.record_quote"), \
             patch("db.resolve_pending_clarification") as mock_resolve, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": rfq_resolved_id, "price": 68.0, "delivery_time": "2 days"}
             }):

            payload = make_whatsapp_payload("the 2 hour one")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            mock_resolve.assert_called_once_with("clarif-session-12")
            mock_update_rfq.assert_called_with("inbound-msg-12", rfq_resolved_id)

    # --------------------------------------------------------------------------
    # 13. Original extracted price survives clarification
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_13_original_extracted_price_survives_clarification(self):
        """
        13. Turn 1: Supplier sends: "68 AED per piece, delivery in 2 days."
            Agent asks: "Do you mean the 24-hour order or 2-hour order?"
            Turn 2: Supplier replies: "2-hour one"
            Expected:
            price = 68.0
            delivery = "2 days"
            selected RFQ = 2-hour RFQ
            Agent records quote with price=68 without re-asking price.
        """
        client = TestClient(main.app)
        rfq_2hr_id = "rfq-urgent-2hr"
        rfq_24hr_id = "rfq-standard-24hr"

        cand_rfqs = [
            make_open_rfq(rfq_2hr_id, "PVC Pipe 1 Inch", specs="2-hour deadline"),
            make_open_rfq(rfq_24hr_id, "PVC Pipe 1 Inch", specs="24-hour deadline"),
        ]
        pending = {
            "id": "clarif-session-13",
            "client_id": CLIENT_ID,
            "supplier_id": SUPPLIER_ID,
            "pending_rfq_ids": [rfq_2hr_id, rfq_24hr_id],
            "raw_message": "68 AED per piece, delivery in 2 days.",
            "extracted_price": 68.0,
            "extracted_delivery": "2 days",
            "extracted_notes": None,
            "round_number": 1,
            "status": "awaiting_reply",
            "last_question": "Do you mean the 24-hour order or 2-hour order?",
        }

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Pipe Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        # In Turn 2, Groq resolves the RFQ. Even if price is omitted in the tool arguments, validator backfills it.
        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_pending_clarification_for_supplier", return_value=pending), \
             patch("db.get_rfqs_by_ids", return_value=cand_rfqs), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-msg-13"), \
             patch("db.update_message_related_rfq"), \
             patch("db.record_quote") as mock_record_quote, \
             patch("db.resolve_pending_clarification") as mock_resolve, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": rfq_2hr_id}  # Notice price omitted; backfilled from pending
             }):

            payload = make_whatsapp_payload("2-hour one")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded_from_clarification"
            assert resp.json()["rfq_id"] == rfq_2hr_id

            # Verify quote was recorded with the preserved price=68.0 and delivery="2 days"
            mock_record_quote.assert_called_once_with(
                rfq_id=rfq_2hr_id,
                supplier_id=SUPPLIER_ID,
                price=68.0,
                delivery_time="2 days",
                quality_notes=None,
                raw_message="2-hour one",
            )
            mock_resolve.assert_called_once_with("clarif-session-13")

    # --------------------------------------------------------------------------
    # 14. Revision to an older responded RFQ before deadline still works
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_14_revision_to_older_responded_rfq_before_deadline_still_works(self):
        """14. A quote revision sent for an older responded RFQ before its deadline is recorded as a revision."""
        client = TestClient(main.app)
        old_rfq_id = "rfq-old-responded"
        old_rfq_entry = make_open_rfq(old_rfq_id, "PVC Pipe 1 Inch", status="responded", hours_remaining=5)

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}
        prior_quotes = [{"rfq_id": old_rfq_id, "price": 45.0, "delivery_time": "3 days"}]

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=old_rfq_entry), \
             patch("db.get_open_rfqs_for_supplier", return_value=[old_rfq_entry]), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=prior_quotes), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-msg-14"), \
             patch("db.update_message_related_rfq"), \
             patch("db.record_quote") as mock_record_quote, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": old_rfq_id, "price": 43.0, "delivery_time": "2 days"}
             }):

            payload = make_whatsapp_payload("Actually we can do 43 AED and 2 days delivery", stanza_id="STANZA-OLD-RFQ")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded_via_quoted_message"
            assert resp.json()["rfq_id"] == old_rfq_id
            mock_record_quote.assert_called_once_with(
                rfq_id=old_rfq_id,
                supplier_id=SUPPLIER_ID,
                price=43.0,
                delivery_time="2 days",
                quality_notes=None,
                raw_message="Actually we can do 43 AED and 2 days delivery",
            )

    # --------------------------------------------------------------------------
    # 15. Multiple unrelated old RFQs do not force unnecessary clarification
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_15_multiple_unrelated_old_rfqs_do_not_force_unnecessary_clarification(self):
        """15. When supplier receives a new quote for an unanswered RFQ, multiple unrelated old responded RFQs do not trigger clarification."""
        client = TestClient(main.app)
        rfq_new_id = "rfq-new-water-pump"
        rfq_old1_id = "rfq-old-paint"
        rfq_old2_id = "rfq-old-cement"
        rfq_old3_id = "rfq-old-screws"

        all_rfqs = [
            make_open_rfq(rfq_old1_id, "White Paint 5L", status="responded"),
            make_open_rfq(rfq_old2_id, "Portland Cement", status="responded"),
            make_open_rfq(rfq_old3_id, "Drywall Screws 1000pcs", status="responded"),
            make_open_rfq(rfq_new_id, "Water Pump 1HP", status="sent"),
        ]
        prior_quotes = [
            {"rfq_id": rfq_old1_id, "price": 50.0},
            {"rfq_id": rfq_old2_id, "price": 25.0},
            {"rfq_id": rfq_old3_id, "price": 15.0},
        ]

        mock_client = {"id": CLIENT_ID, "name": "Amafah", "whatsapp_instance": "test_instance"}
        mock_supp = {"id": SUPPLIER_ID, "name": "Mega Supplier", "phone_number": PHONE_NUMBER, "client_id": CLIENT_ID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=all_rfqs), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=prior_quotes), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="inbound-msg-15"), \
             patch("db.update_message_related_rfq"), \
             patch("db.record_quote") as mock_record_quote, \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": rfq_new_id, "price": 350.0, "delivery_time": "1 day"}
             }) as mock_groq:

            payload = make_whatsapp_payload("350 AED, 1 day delivery")
            resp = client.post("/webhook/whatsapp", json=payload)

            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded"
            assert resp.json()["rfq_id"] == rfq_new_id

            # Reasoner must have received only the new unanswered RFQ (no generic clarification forced)
            called_ctx = mock_groq.call_args[0][1]
            assert len(called_ctx.open_rfqs) == 1
            assert called_ctx.open_rfqs[0]["rfqs"]["id"] == rfq_new_id
            mock_record_quote.assert_called_once()
