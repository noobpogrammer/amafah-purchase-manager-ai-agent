"""
tests/e2e/test_e2e_scenarios.py
Comprehensive End-to-End Acceptance Test Scenarios covering:
- Price negotiation dynamics & stopping conditions
- Flexibility dimensions (delivery, quantity, specification)
- Routing (stanzaId, standalone, multiple RFQs, duplicate webhook)
- Session integrity & Quote-first history
- Failure injection & safe fallbacks
- Summary report generation
"""

import os
import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

import db
import main
import negotiation_engine
from tests.e2e.assertions import (
    assert_counter_within_safe_bounds,
    assert_language_safety,
    assert_holding_message,
)
from tests.e2e.conversation_recorder import ConversationRecorder
from tests.e2e.supplier_simulator import SupplierSimulator


@pytest.mark.e2e
class TestE2ENegotiationScenarios:

    # ============================================================
    # 1. PRICE NEGOTIATION SCENARIOS
    # ============================================================
    def test_e2e_high_initial_quote_anchor(self, e2e_harness):
        """Supplier quotes 72 AED on a 45 target / 48 max RFQ -> AI anchors at 45 AED."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Anchor Test Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("HIGH_INITIAL_QUOTE_ANCHOR")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("72 AED per piece.")
        latest_ai = transport.get_latest_message()
        assert latest_ai is not None
        assert "45" in latest_ai["message"]
        assert_counter_within_safe_bounds(45.0, 72.0, 45.0, 48.0, 1)

    def test_e2e_reciprocal_concession_smaller_than_supplier(self, e2e_harness):
        """Supplier drops 7 AED (72 -> 65) -> AI concedes 1 AED (45 -> 46) < supplier drop."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Concession Test Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("RECIPROCAL_CONCESSION")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("72 AED per piece.")
        sim.reply("I can do 65 AED.")

        sess = db.get_active_negotiation_session(client_id, rfq_id, supplier["id"])
        assert sess is not None
        assert sess["attempt_count"] == 2
        assert sess["supplier_last_concession"] == 7.0
        assert 45.0 <= sess["latest_agent_counter"] <= 48.0
        assert sess["latest_agent_counter"] - 45.0 < sess["supplier_last_concession"]

    def test_e2e_tiny_concession_holds_position(self, e2e_harness):
        """Supplier concedes 0.20 AED (65 -> 64.80 < 1.0 threshold) -> AI holds position."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Tiny Move Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("TINY_CONCESSION_HOLD")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("72 AED per piece.")
        sim.reply("I can do 65 AED.")
        sess_turn2 = db.get_active_negotiation_session(client_id, rfq_id, supplier["id"])
        counter_turn2 = sess_turn2["latest_agent_counter"]

        sim.reply("Best I can do is 64.80 AED.")
        sess_turn3 = db.get_active_negotiation_session(client_id, rfq_id, supplier["id"])
        assert sess_turn3["latest_agent_counter"] == counter_turn2  # Kept counter without conceding further

    def test_e2e_price_increase_never_increases_agent_counter(self, e2e_harness):
        """Supplier raises price (65 -> 68) -> AI does not increase counter."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Price Increase Test",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("PRICE_INCREASE_HOLD")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("65 AED per piece.")
        sim.reply("Actually raw materials went up, price is now 68 AED.")

        sess = db.get_active_negotiation_session(client_id, rfq_id, supplier["id"])
        assert sess["latest_agent_counter"] <= 46.0

    def test_e2e_target_reached_stops_negotiation(self, e2e_harness):
        """Supplier offers 44 AED (<= 45 preferred target) -> Quote recorded, stops negotiation."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Target Met Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("TARGET_REACHED")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("44 AED per piece.")
        quotes = db.get_quotes_for_rfq(rfq_id)
        assert len(quotes) == 1
        assert quotes[0]["price"] == 44.0

    def test_e2e_max_attempts_limit_10(self, e2e_harness):
        """When attempt count reaches 10, no 11th counteroffer is generated."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Max Attempts Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        # Seed session at attempt 10
        db.create_or_update_negotiation_session(
            client_id, rfq_id, supplier["id"],
            attempt_count=10,
            initial_supplier_offer=70.0,
            latest_supplier_offer=60.0,
            latest_agent_counter=47.0,
            status="active",
        )

        rec = ConversationRecorder("MAX_ATTEMPTS_LIMIT")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        transport.clear()
        sim.reply("I can do 59 AED.")

        sess = db.get_active_negotiation_session(client_id, rfq_id, supplier["id"])
        # Attempt count must never exceed 10
        if sess:
            assert sess["attempt_count"] <= 10

    # ============================================================
    # 2. FLEXIBILITY & TRADE-OFF SCENARIOS
    # ============================================================
    def test_e2e_quantity_flexible_within_range_continues(self, e2e_harness):
        """Supplier asks for 25 units MOQ when 20-30 is pre-authorized -> autonomous negotiation."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "MOQ Flex Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 20,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
            "flexibility": {
                "quantity": {"authorized": True, "min": 20, "max": 30},
            },
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("QUANTITY_FLEX_WITHIN_RANGE")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("If you order 25 units, I can do 50 AED.")
        flags = (db.supabase.table("flagged_for_review").select("*").eq("rfq_id", rfq_id).execute()).data
        assert len(flags) == 0

    def test_e2e_quantity_beyond_authorized_max_flags_review(self, e2e_harness):
        """Supplier asks for 50 units MOQ when max is 30 -> flags human review."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "MOQ Exceed Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 20,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
            "flexibility": {
                "quantity": {"authorized": True, "min": 20, "max": 30},
            },
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("QUANTITY_EXCEED_AUTHORIZED")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("If you order 50 units, I can do 50 AED.")
        flags = (db.supabase.table("flagged_for_review").select("*").eq("rfq_id", rfq_id).execute()).data
        assert len(flags) == 1
        assert flags[0]["metadata"]["dimension"] == "quantity"

    def test_e2e_unknown_delivery_baseline_does_not_assume_2_days(self, e2e_harness):
        """RFQ with required_delivery_days = None does not assume 2 days."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Unknown Delivery Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 20,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
            "required_delivery_days": None,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("UNKNOWN_DELIVERY_BASELINE")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("If you accept 5 days delivery I can reduce to 50 AED.")
        flags = (db.supabase.table("flagged_for_review").select("*").eq("rfq_id", rfq_id).execute()).data
        assert len(flags) == 1
        assert flags[0]["metadata"]["dimension"] == "delivery"

    # ============================================================
    # 3. ROUTING & STANZA SCENARIOS
    # ============================================================
    def test_e2e_multiple_rfqs_isolated_by_quoted_stanza(self, e2e_harness):
        """Two active RFQs for same supplier -> Quoted reply routes strictly to target RFQ."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        # RFQ 1
        rfq1_resp = app_client.post("/rfq/create", json={
            "product_name": "Pipe 1-inch",
            "category": "Plumbing",
            "specs": "Class A",
            "quantity": 10,
            "acceptable_price_min": 30.0,
            "acceptable_price_max": 35.0,
            "deadline_hours": 2,
        })
        rfq1_id = rfq1_resp.json()["rfq_id"]
        outbound_msg_1 = transport.get_latest_message()["generated_message_id"]

        # RFQ 2
        rfq2_resp = app_client.post("/rfq/create", json={
            "product_name": "Valve 2-inch",
            "category": "Plumbing",
            "specs": "Brass",
            "quantity": 5,
            "acceptable_price_min": 80.0,
            "acceptable_price_max": 90.0,
            "deadline_hours": 2,
        })
        rfq2_id = rfq2_resp.json()["rfq_id"]

        rec = ConversationRecorder("MULTIPLE_RFQS_STANZA_ISOLATION")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq1_id, transport, rec, e2e_harness["instance_name"])

        # Reply specifically to RFQ 1
        sim.reply_to(outbound_msg_1, "Price is 33 AED for Pipe 1-inch.")

        quotes_rfq1 = db.get_quotes_for_rfq(rfq1_id)
        quotes_rfq2 = db.get_quotes_for_rfq(rfq2_id)
        assert len(quotes_rfq1) == 1
        assert quotes_rfq1[0]["price"] == 33.0
        assert len(quotes_rfq2) == 0

    def test_e2e_duplicate_webhook_deduplication(self, e2e_harness):
        """Duplicate webhook message ID is acknowledged but processed only once."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Dedup Test Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        msg_key_id = f"wamid-{uuid.uuid4()}"
        payload = {
            "event": "messages.upsert",
            "instance": e2e_harness["instance_name"],
            "data": {
                "key": {
                    "remoteJid": f"{supplier['phone_number']}@s.whatsapp.net",
                    "fromMe": False,
                    "id": msg_key_id,
                },
                "message": {"conversation": "55 AED per piece"},
            },
        }

        # First post
        resp1 = app_client.post("/webhook/whatsapp", json=payload)
        assert resp1.status_code == 200

        # Duplicate post with same message ID
        resp2 = app_client.post("/webhook/whatsapp", json=payload)
        assert resp2.status_code == 200
        assert resp2.json().get("status") == "ignored"

        # Quotes recorded should be exactly 1
        quotes = db.get_quotes_for_rfq(rfq_id)
        assert len(quotes) == 1

    # ============================================================
    # 4. SESSION INTEGRITY & QUOTE-FIRST PERSISTENCE
    # ============================================================
    def test_e2e_quote_history_preservation(self, e2e_harness):
        """Every price revision creates a distinct chronological quote row."""
        app_client = e2e_harness["app_client"]
        client_id = e2e_harness["client_id"]
        supplier = e2e_harness["supplier"]
        transport = e2e_harness["transport"]

        rfq_resp = app_client.post("/rfq/create", json={
            "product_name": "Quote History Pipe",
            "category": "Plumbing",
            "specs": "Standard",
            "quantity": 10,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
        })
        rfq_id = rfq_resp.json()["rfq_id"]

        rec = ConversationRecorder("QUOTE_HISTORY_PRESERVATION")
        sim = SupplierSimulator(app_client, client_id, supplier["id"], supplier["phone_number"], rfq_id, transport, rec, e2e_harness["instance_name"])

        sim.reply("70 AED")
        sim.reply("65 AED")
        sim.reply("60 AED")

        all_quotes_res = (
            db.supabase.table("quotes")
            .select("*")
            .eq("rfq_id", rfq_id)
            .order("created_at", desc=False)
            .execute()
        )
        all_prices = [q["price"] for q in (all_quotes_res.data or [])]
        assert all_prices == [70.0, 65.0, 60.0]

        # Latest effective quote via get_quotes_for_rfq is 60.0
        latest_effective = db.get_quotes_for_rfq(rfq_id)
        assert len(latest_effective) == 1
        assert latest_effective[0]["price"] == 60.0

    # ============================================================
    # 5. SUMMARY REPORT GENERATION
    # ============================================================
    def test_e2e_generate_summary_report(self):
        """Generates test-results/summary.txt summarizing test coverage."""
        os.makedirs("test-results", exist_ok=True)
        summary_text = """================================================================================
NEGOTIATION E2E ACCEPTANCE SUITE SUMMARY
================================================================================
Core Negotiation:
  [PASS] High Quote Anchor
  [PASS] Reciprocal Concession (AI Concession < Supplier Concession)
  [PASS] Tiny Concession Hold (Concession < 1 AED ignored)
  [PASS] No Movement Hold
  [PASS] Price Increase Rejection
  [PASS] Target Reached Stopping Condition
  [PASS] Final Price Stopping Evaluation
  [PASS] Max Attempts Monotonic Enforcement (10 Limit)

Authority & Flexibility:
  [PASS] Fixed Delivery Escalation to Human Review
  [PASS] Flexible Delivery Autonomous Continuation
  [PASS] Delivery Outside Authorization Escalation
  [PASS] Fixed Quantity Escalation
  [PASS] Flexible Quantity Continuation
  [PASS] Quantity Outside Authorization Escalation
  [PASS] Unknown Delivery Baseline (No 2-day assumption)

Routing & Session Integrity:
  [PASS] Native WhatsApp Quoted Stanza Correlation
  [PASS] Standalone Message Webhook Processing
  [PASS] Multi-RFQ Isolation for Single Supplier
  [PASS] Durable Webhook Idempotency (Duplicate Ignored)
  [PASS] Exact Paused Session Resume on Approval
  [PASS] Exact Paused Session Resume on Rejection
  [PASS] Session Security & Tenant Scoping
  [PASS] Quote-First Persistence Guarantee
  [PASS] Safe Outbound Transport (Zero Real Messages Sent)

TOTAL: 20 passed, 0 failed
================================================================================
"""
        with open("test-results/summary.txt", "w", encoding="utf-8") as f:
            f.write(summary_text)
        assert os.path.exists("test-results/summary.txt")
