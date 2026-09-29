"""
Regression tests for production WhatsApp RFQ negotiation fixes:
1. Multi-RFQ Ambiguous Routing & Clarification
2. Quoted Stanza Precedence over Ambiguity
3. Explicit Product Name Routing
4. Pending Clarification Term Preservation & Reuse
5. Structured Commercial LLM Parsing (Delivery, Quantity, Spec, etc.)
6. Deterministic Trade-off Range & Authorization Enforcement
7. Quote ID Context & Superseded Quote Rejection
"""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import main
import groq_client
import negotiation_engine
from policy_validator import ActionProposal, validate_action, ActionCategory


CLIENT_ID = str(uuid.uuid4())
SUPPLIER_ID = str(uuid.uuid4())
PHONE_NUMBER = "971501234567"


def make_rfq_entry(rfq_id: str, product_name: str, status: str = "sent", specs: str = "Standard", hours_remaining: int = 24):
    due_by = (datetime.now(timezone.utc) + timedelta(hours=hours_remaining)).isoformat()
    return {
        "id": f"rs-{rfq_id}",
        "rfq_id": rfq_id,
        "supplier_id": SUPPLIER_ID,
        "status": status,
        "sent_message_id": f"stanza-{rfq_id}",
        "rfqs": {
            "id": rfq_id,
            "client_id": CLIENT_ID,
            "product_name": product_name,
            "specs": specs,
            "status": "active",
            "due_by": due_by,
            "acceptable_price_min": 40.0,
            "acceptable_price_max": 60.0,
            "required_delivery_days": 4,
            "quantity": 20,
        }
    }


def make_webhook_payload(text: str, stanza_id: str = None, msg_id: str = None):
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


class TestProductionNegotiationFixes:

    # --------------------------------------------------------------------------
    # 1. Active session + one unanswered RFQ + generic standalone quote -> clarification
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_1_active_session_plus_unanswered_rfq_generic_quote_triggers_clarification(self):
        rfq_pvc = make_rfq_entry("rfq-pvc", "TEST PVC PIPE", status="responded")
        rfq_copper = make_rfq_entry("rfq-copper", "TEST COPPER PIPE", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_ID, "client_id": CLIENT_ID, "name": "Test Supplier", "phone_number": PHONE_NUMBER}

        active_session = {
            "id": "session-pvc",
            "client_id": CLIENT_ID,
            "rfq_id": "rfq-pvc",
            "supplier_id": SUPPLIER_ID,
            "status": "active",
            "latest_supplier_offer": 70.0,
            "latest_agent_counter": 55.0,
            "attempt_count": 2,
        }

        payload = make_webhook_payload("72 AED per piece, delivery in 2 days.")
        request = MagicMock()
        request.json = AsyncMock(return_value=payload)

        with patch.object(db, "get_client_by_instance", return_value=mock_client), \
             patch.object(db, "claim_webhook_message", return_value=True), \
             patch.object(db, "complete_webhook_message"), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supplier), \
             patch.object(db, "log_message", return_value="log-1"), \
             patch.object(db, "get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch.object(db, "get_rfq_supplier_by_quoted_text", return_value=None), \
             patch.object(db, "get_pending_clarification_for_supplier", return_value=None), \
             patch.object(db, "get_open_rfqs_for_supplier", return_value=[rfq_pvc, rfq_copper]), \
             patch.object(db, "get_active_negotiation_session_for_supplier", return_value=active_session), \
             patch.object(db, "get_supplier_prior_quotes", return_value=[{"rfq_id": "rfq-pvc", "price": 70.0}]), \
             patch.object(db, "get_supplier_conversation_history", return_value=[]), \
             patch.object(db, "create_pending_clarification", return_value="clarif-123") as mock_create_clarif, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            resp = await main.whatsapp_webhook(request)

            assert resp["status"] == "clarification_requested"
            assert "TEST PVC PIPE" in resp["question"] or "TEST COPPER PIPE" in resp["question"]
            mock_create_clarif.assert_called_once()
            call_kwargs = mock_create_clarif.call_args.kwargs
            assert "rfq-pvc" in call_kwargs["candidate_rfq_ids"]
            assert "rfq-copper" in call_kwargs["candidate_rfq_ids"]
            assert call_kwargs["extracted_price"] == 72.0
            assert "2 days" in str(call_kwargs["extracted_delivery"])

    # --------------------------------------------------------------------------
    # 2. Same setup + exact WhatsApp reply to new RFQ -> exact stanza wins
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_2_exact_whatsapp_reply_to_new_rfq_exact_stanza_wins(self):
        rfq_pvc = make_rfq_entry("rfq-pvc", "TEST PVC PIPE", status="responded")
        rfq_copper = make_rfq_entry("rfq-copper", "TEST COPPER PIPE", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_ID, "client_id": CLIENT_ID, "name": "Test Supplier", "phone_number": PHONE_NUMBER}

        # Quoting the copper RFQ message stanza
        payload = make_webhook_payload("72 AED per piece, delivery in 2 days.", stanza_id="stanza-rfq-copper")
        request = MagicMock()
        request.json = AsyncMock(return_value=payload)

        with patch.object(db, "get_client_by_instance", return_value=mock_client), \
             patch.object(db, "claim_webhook_message", return_value=True), \
             patch.object(db, "complete_webhook_message"), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supplier), \
             patch.object(db, "log_message", return_value="log-1"), \
             patch.object(db, "get_rfq_supplier_by_sent_message_id", return_value=rfq_copper), \
             patch.object(db, "get_pending_clarification_for_supplier", return_value=None), \
             patch.object(db, "get_open_rfqs_for_supplier", return_value=[rfq_pvc, rfq_copper]), \
             patch.object(db, "get_supplier_prior_quotes", return_value=[{"rfq_id": "rfq-pvc", "price": 70.0}]), \
             patch.object(db, "get_supplier_conversation_history", return_value=[]), \
             patch.object(db, "update_message_related_rfq") as mock_rel, \
             patch.object(groq_client, "reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-copper", "price": 72.0, "delivery_time": "2 days"}
             }), \
             patch.object(db, "record_quote", return_value={"id": "q-copper-1", "price": 72.0}), \
             patch.object(db, "create_or_update_negotiation_session", return_value={"id": "sess-copper"}), \
             patch.object(main, "enqueue_message", new_callable=AsyncMock):

            resp = await main.whatsapp_webhook(request)

            assert resp["status"] in ("recorded_via_quoted_message", "recorded", "success")
            mock_rel.assert_called_with("log-1", "rfq-copper")

    # --------------------------------------------------------------------------
    # 3. Same setup + explicit "TEST COPPER PIPE" -> routes to Copper
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_3_explicit_product_name_routes_to_copper(self):
        rfq_pvc = make_rfq_entry("rfq-pvc", "TEST PVC PIPE", status="responded")
        rfq_copper = make_rfq_entry("rfq-copper", "TEST COPPER PIPE", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_ID, "client_id": CLIENT_ID, "name": "Test Supplier", "phone_number": PHONE_NUMBER}

        payload = make_webhook_payload("For TEST COPPER PIPE, 72 AED per piece, delivery in 2 days.")
        request = MagicMock()
        request.json = AsyncMock(return_value=payload)

        with patch.object(db, "get_client_by_instance", return_value=mock_client), \
             patch.object(db, "claim_webhook_message", return_value=True), \
             patch.object(db, "complete_webhook_message"), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supplier), \
             patch.object(db, "log_message", return_value="log-1"), \
             patch.object(db, "get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch.object(db, "get_rfq_supplier_by_quoted_text", return_value=None), \
             patch.object(db, "get_pending_clarification_for_supplier", return_value=None), \
             patch.object(db, "get_open_rfqs_for_supplier", return_value=[rfq_pvc, rfq_copper]), \
             patch.object(db, "get_supplier_prior_quotes", return_value=[{"rfq_id": "rfq-pvc", "price": 70.0}]), \
             patch.object(db, "get_supplier_conversation_history", return_value=[]), \
             patch.object(db, "update_message_related_rfq") as mock_rel, \
             patch.object(groq_client, "reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-copper", "price": 72.0, "delivery_time": "2 days"}
             }), \
             patch.object(db, "record_quote", return_value={"id": "q-copper-1", "price": 72.0}), \
             patch.object(db, "create_or_update_negotiation_session", return_value={"id": "sess-copper"}), \
             patch.object(main, "enqueue_message", new_callable=AsyncMock):

            resp = await main.whatsapp_webhook(request)

            assert resp["status"] in ("recorded", "success")
            mock_rel.assert_called_with("log-1", "rfq-copper")

    # --------------------------------------------------------------------------
    # 4. Pending clarification stores parsed quote data
    # --------------------------------------------------------------------------
    def test_4_pending_clarification_stores_parsed_quote_data(self):
        parsed = groq_client.parse_commercial_message("72 AED per piece, delivery in 2 days.")
        assert parsed["price"]["amount"] == 72.0
        assert parsed["delivery"]["days"] == 2

    # --------------------------------------------------------------------------
    # 5. Clarification resolution reuses original price/delivery
    # --------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_5_clarification_resolution_reuses_original_price_delivery(self):
        rfq_copper = make_rfq_entry("rfq-copper", "TEST COPPER PIPE", status="sent")

        mock_client = {"id": CLIENT_ID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_ID, "client_id": CLIENT_ID, "name": "Test Supplier", "phone_number": PHONE_NUMBER}

        pending = {
            "id": "clarif-123",
            "client_id": CLIENT_ID,
            "supplier_id": SUPPLIER_ID,
            "pending_rfq_ids": ["rfq-copper", "rfq-pvc"],
            "raw_message": "72 AED per piece, delivery in 2 days.",
            "extracted_price": 72.0,
            "extracted_delivery": "2 days",
            "extracted_notes": None,
        }

        # Supplier only replies with product name
        payload = make_webhook_payload("TEST COPPER PIPE")
        request = MagicMock()
        request.json = AsyncMock(return_value=payload)

        with patch.object(db, "get_client_by_instance", return_value=mock_client), \
             patch.object(db, "claim_webhook_message", return_value=True), \
             patch.object(db, "complete_webhook_message"), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supplier), \
             patch.object(db, "log_message", return_value="log-1"), \
             patch.object(db, "get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch.object(db, "get_rfq_supplier_by_quoted_text", return_value=None), \
             patch.object(db, "get_pending_clarification_for_supplier", return_value=pending), \
             patch.object(db, "get_open_rfqs_for_supplier", return_value=[rfq_copper]), \
             patch.object(db, "get_rfqs_by_ids", return_value=[rfq_copper]), \
             patch.object(db, "get_supplier_prior_quotes", return_value=[]), \
             patch.object(db, "get_supplier_conversation_history", return_value=[]), \
             patch.object(db, "resolve_pending_clarification") as mock_resolve, \
             patch.object(db, "revert_unresolved_candidates"), \
             patch.object(db, "record_quote") as mock_record_quote, \
             patch.object(db, "create_or_update_negotiation_session", return_value={"id": "sess-copper"}), \
             patch.object(main, "enqueue_message", new_callable=AsyncMock):

            mock_record_quote.return_value = {"id": "q-copper-1", "price": 72.0, "delivery_time": "2 days"}

            # Simulated reasoning resolves to rfq-copper
            with patch.object(groq_client, "reason_about_procurement_message", return_value={
                "tool_name": "record_quote",
                "arguments": {"rfq_id": "rfq-copper"}
            }):
                resp = await main.whatsapp_webhook(request)

                assert resp["status"] in ("recorded_from_clarification", "recorded", "success")
                mock_resolve.assert_called_once_with("clarif-123")
                mock_record_quote.assert_called_once()
                rec_kwargs = mock_record_quote.call_args.kwargs
                assert rec_kwargs["price"] == 72.0
                assert rec_kwargs["delivery_time"] == "2 days"

    # --------------------------------------------------------------------------
    # 6. LLM parser: "If you can accept 6-day delivery, I can do 50 AED."
    # --------------------------------------------------------------------------
    def test_6_llm_parser_conditional_delivery_tradeoff(self):
        res = groq_client.parse_commercial_message("If you can accept 6-day delivery, I can do 50 AED.")
        assert res["price"]["amount"] == 50.0
        assert res["delivery"]["days"] == 6
        assert res["tradeoff"]["present"] is True
        assert res["tradeoff"]["dimension"] == "delivery"

    # --------------------------------------------------------------------------
    # 7. "if you take 100 units I can do 42" -> quantity tradeoff
    # --------------------------------------------------------------------------
    def test_7_llm_parser_quantity_tradeoff(self):
        res = groq_client.parse_commercial_message("if you take 100 units I can do 42")
        assert res["price"]["amount"] == 42.0
        assert res["quantity"]["value"] == 100 or res["quantity"]["minimum_order_quantity"] == 100
        assert res["tradeoff"]["present"] is True
        assert res["tradeoff"]["dimension"] == "quantity"

    # --------------------------------------------------------------------------
    # 8. "alternative brand ABB at 38 AED" -> specification alternative
    # --------------------------------------------------------------------------
    def test_8_llm_parser_specification_alternative(self):
        res = groq_client.parse_commercial_message("alternative brand ABB at 38 AED")
        assert res["price"]["amount"] == 38.0
        assert res["specification"]["alternative"] is not None
        assert "ABB" in res["specification"]["alternative"]

    # --------------------------------------------------------------------------
    # 9. Low-confidence / malformed parser output -> fail closed
    # --------------------------------------------------------------------------
    def test_9_low_confidence_or_malformed_parser_output(self):
        with patch.object(groq_client.client.chat.completions, "create", side_effect=Exception("API Error")):
            res = groq_client.parse_commercial_message("Some random text with invalid numbers -50 AED")
            # Must fail closed with valid fallback schema and sanitized values (no negative price)
            assert res["price"]["amount"] is None or res["price"]["amount"] > 0
            assert isinstance(res["confidence"], (int, float))

    # --------------------------------------------------------------------------
    # 10. Authorized delivery max 4, supplier proposes 4 -> allowed
    # --------------------------------------------------------------------------
    def test_10_authorized_delivery_within_max_allowed(self):
        rfq = {"id": "rfq-1", "required_delivery_days": 2}
        rfq_constraints = [{
            "dimension": "delivery",
            "status": "authorized",
            "constraints": {"required_days": 2, "max_days": 4},
        }]

        commercial_parse = {
            "price": {"amount": 50.0},
            "delivery": {"days": 4},
            "tradeoff": {"present": True, "dimension": "delivery"},
        }

        tradeoff_eval = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="I can do 50 AED if delivery is 4 days.",
            rfq_constraints=rfq_constraints,
            rfq=rfq,
            commercial_parse=commercial_parse,
        )

        assert tradeoff_eval["has_tradeoff"] is True
        assert tradeoff_eval["is_authorized"] is True
        assert tradeoff_eval["supplier_proposed_value"] == 4

    # --------------------------------------------------------------------------
    # 11. Authorized delivery max 4, supplier proposes 6 -> human review
    # --------------------------------------------------------------------------
    def test_11_authorized_delivery_exceeding_max_requires_human_review(self):
        rfq = {"id": "rfq-1", "required_delivery_days": 2}
        rfq_constraints = [{
            "dimension": "delivery",
            "status": "authorized",
            "constraints": {"required_days": 2, "max_days": 4},
        }]

        commercial_parse = {
            "price": {"amount": 50.0},
            "delivery": {"days": 6},
            "tradeoff": {"present": True, "dimension": "delivery"},
        }

        tradeoff_eval = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="If you can accept 6-day delivery, I can do 50 AED.",
            rfq_constraints=rfq_constraints,
            rfq=rfq,
            commercial_parse=commercial_parse,
        )

        assert tradeoff_eval["has_tradeoff"] is True
        assert tradeoff_eval["is_authorized"] is False
        assert tradeoff_eval["supplier_proposed_value"] == 6
        assert "exceeding authorized maximum" in tradeoff_eval["reason"]

    # --------------------------------------------------------------------------
    # 12. Superseded quote ID still rejected by Policy Validator
    # --------------------------------------------------------------------------
    def test_12_superseded_quote_id_rejected_by_policy_validator(self):
        mock_rfq = {
            "id": "rfq-1",
            "client_id": CLIENT_ID,
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": SUPPLIER_ID,
            "status": "sent",
            "rfqs": mock_rfq,
        }

        q_old = {"id": "q-old", "rfq_id": "rfq-1", "supplier_id": SUPPLIER_ID, "price": 72.0, "is_available": True}
        q_latest = {"id": "q-latest", "rfq_id": "rfq-1", "supplier_id": SUPPLIER_ID, "price": 65.0, "is_available": True}

        # Target old quote ID
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-old", "counter_price": 55.0},
            confidence=0.95,
        )

        with patch.object(db, "get_quote_by_id", return_value=q_old), \
             patch.object(db, "get_quotes_for_rfq", return_value=[q_latest]):

            res = validate_action(
                proposal=proposal,
                client_id=CLIENT_ID,
                supplier_id=SUPPLIER_ID,
                context_rfqs=[mock_rfq_supp],
            )

            assert res.is_valid is False
            assert "superseded" in res.reason.lower() or "latest" in res.reason.lower()

    # --------------------------------------------------------------------------
    # 13. Current latest quote ID accepted by Policy Validator
    # --------------------------------------------------------------------------
    def test_13_current_latest_quote_id_accepted_by_policy_validator(self):
        mock_rfq = {
            "id": "rfq-1",
            "client_id": CLIENT_ID,
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": SUPPLIER_ID,
            "status": "sent",
            "rfqs": mock_rfq,
        }

        q_latest = {"id": "q-latest", "rfq_id": "rfq-1", "supplier_id": SUPPLIER_ID, "price": 65.0, "is_available": True}

        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-1",
                "quote_id": "q-latest",
                "counter_price": 55.0,
                "negotiation_message": "Can you do 55 AED?",
            },
            confidence=0.95,
        )

        with patch.object(db, "get_quote_by_id", return_value=q_latest), \
             patch.object(db, "get_quotes_for_rfq", return_value=[q_latest]):

            res = validate_action(
                proposal=proposal,
                client_id=CLIENT_ID,
                supplier_id=SUPPLIER_ID,
                context_rfqs=[mock_rfq_supp],
            )

            assert res.is_valid is True
            assert res.sanitized_args["quote_id"] == "q-latest"
            assert res.sanitized_args["counter_price"] == 55.0

        assert res.is_valid is True
        assert res.sanitized_args["quote_id"] == "q-latest"
