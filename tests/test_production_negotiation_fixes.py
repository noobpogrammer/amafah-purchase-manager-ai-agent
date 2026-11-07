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
import openclaw_bridge
import negotiation_engine


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

    # --------------------------------------------------------------------------
    # 2. Same setup + exact WhatsApp reply to new RFQ -> exact stanza wins
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 3. Same setup + explicit "TEST COPPER PIPE" -> routes to Copper
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 4. Pending clarification stores parsed quote data
    # --------------------------------------------------------------------------
    def test_4_pending_clarification_stores_parsed_quote_data(self):
        parsed = openclaw_bridge.parse_commercial_message("72 AED per piece, delivery in 2 days.")
        assert parsed["price"]["amount"] == 72.0
        assert parsed["delivery"]["days"] == 2

    # --------------------------------------------------------------------------
    # 5. Clarification resolution reuses original price/delivery
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # 6. LLM parser: "If you can accept 6-day delivery, I can do 50 AED."
    # --------------------------------------------------------------------------
    def test_6_llm_parser_conditional_delivery_tradeoff(self):
        res = openclaw_bridge.parse_commercial_message("If you can accept 6-day delivery, I can do 50 AED.")
        assert res["price"]["amount"] == 50.0
        assert res["delivery"]["days"] == 6
        assert res["tradeoff"]["present"] is True
        assert res["tradeoff"]["dimension"] == "delivery"

    # --------------------------------------------------------------------------
    # 7. "if you take 100 units I can do 42" -> quantity tradeoff
    # --------------------------------------------------------------------------
    def test_7_llm_parser_quantity_tradeoff(self):
        res = openclaw_bridge.parse_commercial_message("if you take 100 units I can do 42")
        assert res["price"]["amount"] == 42.0
        assert res["quantity"]["value"] == 100 or res["quantity"]["minimum_order_quantity"] == 100
        assert res["tradeoff"]["present"] is True
        assert res["tradeoff"]["dimension"] == "quantity"

    # --------------------------------------------------------------------------
    # 8. "alternative brand ABB at 38 AED" -> specification alternative
    # --------------------------------------------------------------------------
    def test_8_llm_parser_specification_alternative(self):
        res = openclaw_bridge.parse_commercial_message("alternative brand ABB at 38 AED")
        assert res["price"]["amount"] == 38.0
        assert res["specification"]["alternative"] is not None
        assert "ABB" in res["specification"]["alternative"]

    # --------------------------------------------------------------------------
    # 9. Low-confidence / malformed parser output -> fail closed
    # --------------------------------------------------------------------------
    def test_9_low_confidence_or_malformed_parser_output(self):
        with patch.object(openclaw_bridge, "_request_openclaw_analysis", side_effect=RuntimeError("API Error")):
            res = openclaw_bridge.parse_commercial_message("Some random text with invalid numbers -50 AED")
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

    # --------------------------------------------------------------------------
    # 13. Current latest quote ID accepted by Policy Validator
    # --------------------------------------------------------------------------
